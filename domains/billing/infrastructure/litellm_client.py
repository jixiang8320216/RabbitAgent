"""LiteLLM 客户端：跨 provider 的统一调用与用量归集。

工程里已有 ``domains.llm.provider`` 负责真正的模型调用。这个模块的
定位不同 —— 它解决的是**成本视角**的问题：

1. 用一次调用拿到「跨 provider 的统一用量口径」（prompt/completion/cached token）
2. 把用量折算成金额，喂给 :mod:`domains.billing.domain.litellm_budget`
3. 在超预算时给出明确信号

为什么不直接在 provider 层做计费：provider 层知道各家 API 的原始响应格式，
但「一次调用花了多少钱、这个 agent 累计花了多少」是**跨 provider 的横切关注点**。
放在独立的 billing 域里，provider 层不需要知道预算的存在。

**LiteLLM 是可选依赖**：未安装时本模块的所有能力都降级为「不可用」，
调用方据此跳过计费，不影响主链路。

**历史缺口**：本模块此前是 0 字节文件。
"""

from __future__ import annotations

import logging
import typing

import domains.billing.domain.litellm_budget as budget_model

logger = logging.getLogger(__name__)


def is_litellm_available() -> bool:
    """LiteLLM 是否可用。"""
    try:
        import litellm  # noqa: F401
    except ImportError:
        return False
    return True


class LiteLLMUsage:
    """一次调用的用量（跨 provider 统一口径）。"""

    __slots__ = ("model", "prompt_tokens", "completion_tokens", "cached_tokens")

    def __init__(
        self,
        model: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        self.model = model
        self.prompt_tokens = int(prompt_tokens)
        self.completion_tokens = int(completion_tokens)
        self.cached_tokens = int(cached_tokens)

    @property
    def total_tokens(self) -> int:
        """总 token 数。"""
        return self.prompt_tokens + self.completion_tokens

    def to_record(
        self,
        *,
        scope: budget_model.BudgetScope = budget_model.BudgetScope.GLOBAL,
        scope_key: str = "",
    ) -> budget_model.UsageRecord:
        """转成预算域的用量记录。"""
        return budget_model.UsageRecord(
            model=self.model,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_tokens=self.cached_tokens,
            scope=scope,
            scope_key=scope_key,
        )

    def __repr__(self) -> str:
        return (
            f"LiteLLMUsage(model={self.model!r}, "
            f"prompt={self.prompt_tokens}, completion={self.completion_tokens}, "
            f"cached={self.cached_tokens})"
        )


class LiteLLMClient:
    """LiteLLM 客户端。

    封装两件事：

    - :meth:`complete` —— 发起一次调用并归集用量
    - :meth:`estimate` —— 只算成本不发请求（用于预检是否超预算）
    """

    def __init__(
        self,
        *,
        tracker: budget_model.BudgetTracker | None = None,
        model: str | None = None,
    ) -> None:
        self._tracker = tracker
        self._model = model

    @property
    def tracker(self) -> budget_model.BudgetTracker | None:
        """取关联的预算跟踪器。"""
        return self._tracker

    def estimate(
        self,
        *,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> float | None:
        """估算成本，不发请求。"""
        target = model or self._model
        if not target:
            return None
        return budget_model.estimate_cost(
            target,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    def complete(
        self,
        messages: typing.Sequence[typing.Mapping[str, typing.Any]],
        *,
        model: str | None = None,
        scope: budget_model.BudgetScope = budget_model.BudgetScope.GLOBAL,
        scope_key: str = "",
        **kwargs: typing.Any,
    ) -> LiteLLMUsage:
        """发起一次调用并把用量记入预算。

        :raises RuntimeError: LiteLLM 未安装，或调用失败
        """
        target = model or self._model
        if not target:
            raise ValueError("model is required")
        if not is_litellm_available():
            raise RuntimeError(
                "litellm is not installed; install it with `pip install litellm` "
                "to enable cost tracking"
            )

        import litellm

        response = litellm.completion(model=target, messages=list(messages), **kwargs)
        usage = self._extract_usage(response, target)

        if self._tracker is not None:
            self._tracker.record(usage.to_record(scope=scope, scope_key=scope_key))
        return usage

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _extract_usage(response: typing.Any, model: str) -> LiteLLMUsage:
        """从 LiteLLM 响应里抽用量。

        响应结构在不同 provider 间有差异，这里按
        ``usage.prompt_tokens`` / ``usage.completion_tokens`` 的常见形态取，
        取不到就按 0 处理 —— 用量缺失不该让调用失败。
        """
        raw = getattr(response, "usage", None)
        if raw is None and isinstance(response, dict):
            raw = response.get("usage")
        if raw is None:
            return LiteLLMUsage(model)

        def pick(name: str) -> int:
            value = getattr(raw, name, None)
            if value is None and isinstance(raw, dict):
                value = raw.get(name)
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        cached = pick("cache_read_input_tokens") or pick("cached_tokens")
        return LiteLLMUsage(
            model,
            prompt_tokens=pick("prompt_tokens"),
            completion_tokens=pick("completion_tokens"),
            cached_tokens=cached,
        )


__all__ = [
    "LiteLLMClient",
    "LiteLLMUsage",
    "is_litellm_available",
]
