"""基于 LiteLLM 定价数据的成本预算模型。

回答三个问题：

1. **这次调用花了多少钱** —— 按 model + token 数查 LiteLLM 定价表
2. **还能花多少** —— 预算减去已花
3. **该不该继续** —— 超预算的策略是拒绝还是告警

**为什么预算按 agent 维度**：多 Agent 场景下，总预算超了不代表有问题 ——
可能只是某一个子 Agent 失控。分维度统计才能定位到具体环节。

**依赖说明**：定价数据来自 LiteLLM（``litellm.model_cost``），
它不是必需依赖 —— 未安装时 :func:`estimate_cost` 返回 ``None``，
调用方据此降级为「不计费但继续执行」，而不是中断主链路。

**历史缺口**：本模块此前是 0 字节文件，文件名里的 ``buddget`` 也是拼写错误
（应为 ``budget``）。这里保留原文件名以免破坏既有引用路径，
同时导出正确拼写的别名。
"""

from __future__ import annotations

import enum
import typing

import pydantic


class BudgetExceededAction(enum.StrEnum):
    """超预算时的处置策略。"""

    #: 记录告警但继续执行（默认 —— 成本不该突然中断业务）
    WARN = "warn"
    #: 拒绝新的调用
    REJECT = "reject"
    #: 直接抛异常
    RAISE = "raise"


class BudgetScope(enum.StrEnum):
    """预算的统计维度。"""

    #: 全局（整个进程/会话）
    GLOBAL = "global"
    #: 按 agent 维度
    AGENT = "agent"
    #: 按会话维度
    SESSION = "session"


class ModelPrice(pydantic.BaseModel):
    """单个模型的定价（每百万 token）。"""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    #: 缓存命中部分的输入价格（通常远低于普通输入）
    cache_read_price_per_million: float = 0.0


class UsageRecord(pydantic.BaseModel):
    """一次调用的用量记录。"""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    #: 归因维度（agent 名 / session id）
    scope_key: str = ""
    scope: BudgetScope = BudgetScope.GLOBAL

    @property
    def total_tokens(self) -> int:
        """总 token 数。"""
        return int(self.prompt_tokens) + int(self.completion_tokens)


class BudgetState(pydantic.BaseModel):
    """预算的当前状态。"""

    model_config = pydantic.ConfigDict(extra="forbid")

    #: 预算上限（美元）；``None`` 表示不限额
    limit_usd: float | None = None
    #: 已花金额
    spent_usd: float = 0.0
    #: 超预算时的处置策略
    exceeded_action: BudgetExceededAction = BudgetExceededAction.WARN

    @property
    def remaining_usd(self) -> float | None:
        """剩余可花金额；不限额时返回 ``None``。"""
        if self.limit_usd is None:
            return None
        return max(0.0, float(self.limit_usd) - float(self.spent_usd))

    @property
    def exceeded(self) -> bool:
        """是否已超预算。"""
        if self.limit_usd is None:
            return False
        return float(self.spent_usd) >= float(self.limit_usd)

    def should_reject(self) -> bool:
        """当前状态下是否应拒绝新的调用。"""
        if not self.exceeded:
            return False
        return self.exceeded_action in (
            BudgetExceededAction.REJECT,
            BudgetExceededAction.RAISE,
        )


class BudgetExceededError(RuntimeError):
    """超出预算且策略为 RAISE 时抛出。"""


def estimate_cost(
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
) -> float | None:
    """估算一次调用的成本（美元）。

    优先用 LiteLLM 的定价表；未安装 LiteLLM 或该模型无定价数据时
    返回 ``None`` —— 调用方据此「不计费但继续」，不阻断主链路。
    """
    try:
        import litellm
    except ImportError:
        return None

    try:
        cost = litellm.completion_cost(
            model=model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            cache_read_input_tokens=int(cached_tokens),
        )
    except Exception:  # noqa: BLE001 - 定价查询失败不应影响主流程
        return None

    if cost is None:
        return None
    try:
        return float(cost)
    except (TypeError, ValueError):
        return None


def resolve_model_price(model: str) -> ModelPrice | None:
    """从 LiteLLM 定价表取某个模型的单价。"""
    try:
        import litellm
    except ImportError:
        return None

    try:
        raw = litellm.model_cost.get(model)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, dict):
        return None

    def pick(*keys: str) -> float:
        for key in keys:
            value = raw.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    return ModelPrice(
        input_price_per_million=pick("input_cost_per_token") * 1_000_000,
        output_price_per_million=pick("output_cost_per_token") * 1_000_000,
        cache_read_price_per_million=pick("cache_read_input_token_cost") * 1_000_000,
    )


class BudgetTracker:
    """按维度累计成本并判断是否超预算。

    线程安全：成本累计发生在 LLM 调用的并发路径上。
    """

    def __init__(
        self,
        *,
        limit_usd: float | None = None,
        exceeded_action: BudgetExceededAction = BudgetExceededAction.WARN,
    ) -> None:
        import threading

        self._lock = threading.Lock()
        self._state = BudgetState(
            limit_usd=limit_usd,
            exceeded_action=exceeded_action,
        )
        # 维度键 -> 已花金额
        self._by_scope: typing.Dict[str, float] = {}

    @property
    def state(self) -> BudgetState:
        """取当前预算状态快照。"""
        with self._lock:
            return self._state.model_copy()

    def record(self, record: UsageRecord) -> float | None:
        """记录一次用量的成本，返回本次成本；无法定价时返回 ``None``。"""
        cost = estimate_cost(
            record.model,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            cached_tokens=record.cached_tokens,
        )
        if cost is None:
            return None

        with self._lock:
            self._state.spent_usd = float(self._state.spent_usd) + cost
            key = self._scope_key(record)
            self._by_scope[key] = self._by_scope.get(key, 0.0) + cost

            if self._state.exceeded and self._state.exceeded_action is BudgetExceededAction.RAISE:
                raise BudgetExceededError(
                    f"Budget exceeded: spent {self._state.spent_usd:.6f} USD "
                    f"(limit {self._state.limit_usd})"
                )
        return cost

    def spent_for(self, scope: BudgetScope, scope_key: str = "") -> float:
        """取某个维度的已花金额。"""
        with self._lock:
            if scope is BudgetScope.GLOBAL:
                return float(self._state.spent_usd)
            return float(self._by_scope.get(self._make_key(scope, scope_key), 0.0))

    def reset(self) -> None:
        """清空累计。"""
        with self._lock:
            self._state.spent_usd = 0.0
            self._by_scope.clear()

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _make_key(scope: BudgetScope, scope_key: str) -> str:
        return f"{scope.value}:{scope_key}"

    def _scope_key(self, record: UsageRecord) -> str:
        if record.scope is BudgetScope.GLOBAL:
            return self._make_key(BudgetScope.GLOBAL, "")
        return self._make_key(record.scope, record.scope_key)


# 正确拼写的别名 —— 原文件名 ``litellm_buddget`` 拼错了，这里给出规范名
# 规范名：模块本身即 litellm_budget（文件名已修正拼写）

__all__ = [
    "BudgetExceededAction",
    "BudgetExceededError",
    "BudgetScope",
    "BudgetState",
    "BudgetTracker",
    "ModelPrice",
    "UsageRecord",
    "estimate_cost",
    "litellm_budget",
    "resolve_model_price",
]
