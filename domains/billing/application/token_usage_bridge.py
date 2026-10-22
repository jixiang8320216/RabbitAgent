"""把 token 用量接到预算跟踪器上。

此前 ``domains/billing`` 里有完整的 ``BudgetTracker``（按全局 / agent / session
维度累计成本、可设上限与超预算策略），但**没有任何调用方喂数据**——计费域是
一座孤岛：模型调用的真实用量在 ``TokenCounter`` 里，两侧没有桥。

桥的形状
--------

``TokenCounter`` 每记一次 API 用量，就通过 :class:`BillingUsageSink` 协议把这次
用量推给本模块，由它包装成 ``UsageRecord`` 交给 ``BudgetTracker``。

- 协议定义在**消费方**（llm 域的 token 计数器）里，billing 域提供实现：
  这样 llm 域不依赖 billing 域，谁装配谁对接，跨域依赖只发生在应用层；
- ``estimate_cost`` 在 LiteLLM 不可用 / 模型无定价时返回 ``None``，
  ``BudgetTracker.record`` 也返回 ``None`` —— **不计费但继续**，不阻断主链路；
- 桥自身的任何异常都被吞掉并记 debug：成本统计是旁路，不该让一次对话失败。
"""

from __future__ import annotations

import logging
import typing

import domains.billing.domain.litellm_budget as budget_module

logger = logging.getLogger(__name__)

__all__ = ["BudgetUsageSink"]


class BudgetUsageSink:
    """``BillingUsageSink`` 协议的 billing 侧实现。

    用法::

        tracker = BudgetTracker(limit_usd=5.0)
        token_counter.register_billing_sink(BudgetUsageSink(tracker))
    """

    __slots__ = ("_tracker", "_scope", "_records", "_unknown_models")

    def __init__(
            self,
            tracker: budget_module.BudgetTracker,
            *,
            scope: budget_module.BudgetScope = budget_module.BudgetScope.AGENT,
    ) -> None:
        """
        :param tracker: 预算跟踪器。
        :param scope: 用量归因维度；``AGENT`` 按 agent 名，``GLOBAL`` 只记总量。
        """
        self._tracker = tracker
        self._scope = scope
        self._records = 0
        self._unknown_models = 0

    # -- BillingUsageSink 协议实现 ---------------------------------------

    def record_usage(
            self,
            *,
            model: str,
            prompt_tokens: int,
            completion_tokens: int,
            cached_tokens: int = 0,
            scope_key: str = "",
    ) -> float | None:
        """记一次用量，返回本次成本；无法定价返回 ``None``。"""
        try:
            record = budget_module.UsageRecord(
                model=str(model or ""),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                cached_tokens=int(cached_tokens),
                scope_key=str(scope_key or ""),
                scope=self._scope,
            )
        except Exception as exc:  # noqa: BLE001 - 用量记录构造失败不该影响主流程
            logger.debug("billing: usage record rejected: %s", exc)
            return None

        try:
            cost = self._tracker.record(record)
        except budget_module.BudgetExceededError:
            # 超预算且策略为 RAISE：这是**有意**抛出的，交回调用方处理
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("billing: tracker.record failed: %s", exc)
            return None

        self._records += 1
        if cost is None:
            self._unknown_models += 1
        return cost

    # -- 观测 -------------------------------------------------------------

    @property
    def tracker(self) -> budget_module.BudgetTracker:
        return self._tracker

    def snapshot(self) -> dict[str, typing.Any]:
        """导出桥的统计（日志 / 遥测用）。"""
        return {
            "records": self._records,
            "records_without_price": self._unknown_models,
            "scope": self._scope.value,
            "spent_usd": float(self._tracker.state.spent_usd),
        }
