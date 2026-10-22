"""计费链路的用例：把 token 用量真正喂给 ``BudgetTracker``。

此前 ``billing`` 域有完整的预算跟踪器，但**没有任何数据来源**——真实用量在
``TokenCounter`` 里，两边没桥。用例验证桥接之后用量会流过去，并且：

- LiteLLM 不可用时「不计费但继续」（返回 ``None``，不抛异常）；
- 计费失败不影响 token 计数本身；
- 超预算且策略为 RAISE 时，异常按约定向上抛（这是**有意**的行为，不是意外）。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import domains.billing.application.token_usage_bridge as bridge_module  # noqa: E402
import domains.billing.domain.litellm_budget as budget_module  # noqa: E402
import domains.llm.provider.application.token.token_counter as token_counter_module  # noqa: E402


class _RecordingSink:
    """不依赖 LiteLLM 的假 sink，用来确认「计数器真的推了数据」。"""

    def __init__(self):
        self.calls = []

    def record_usage(self, *, model, prompt_tokens, completion_tokens, cached_tokens=0, scope_key=""):
        self.calls.append({
            "model": model, "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens, "scope_key": scope_key,
        })
        return 0.01


def test_counter_pushes_usage_to_billing_sink():
    counter = token_counter_module.TokenCounter(model_name="gpt-4")
    sink = _RecordingSink()
    counter.register_billing_sink(sink)

    counter.set_api_tokens(agent_name="root", prompt_tokens=1200, completed_tokens=300,
                           cached_tokens=50, operation_kind="normal")

    assert len(sink.calls) == 1
    assert sink.calls[0]["model"] == "gpt-4"
    assert sink.calls[0]["prompt_tokens"] == 1200
    assert sink.calls[0]["completion_tokens"] == 300
    assert sink.calls[0]["scope_key"] == "root"


def test_counter_can_unbind_billing_sink():
    counter = token_counter_module.TokenCounter()
    sink = _RecordingSink()
    counter.register_billing_sink(sink)
    counter.register_billing_sink(None)
    counter.set_api_tokens(agent_name="root", prompt_tokens=1, completed_tokens=1)
    assert sink.calls == []


def test_billing_failure_does_not_break_counting():
    class _Broken:
        def record_usage(self, **kwargs):
            raise RuntimeError("pricing service down")

    counter = token_counter_module.TokenCounter()
    counter.register_billing_sink(_Broken())
    counter.set_api_tokens(agent_name="root", prompt_tokens=10, completed_tokens=5)
    assert int(counter.get_agent_last_tokens("root")["total_tokens"]) == 15


def test_budget_exceeded_error_propagates_through_counter():
    """策略为 RAISE 时，异常必须穿过计数器向上抛（否则策略等于失效）。"""

    class _Exceeded(RuntimeError):
        """用同名的替身类型：llm 域按类型名判定，不依赖 billing 的具体类型。"""

    _Exceeded.__name__ = "BudgetExceededError"

    class _StrictSink:
        def record_usage(self, **kwargs):
            raise _Exceeded("budget exceeded")

    counter = token_counter_module.TokenCounter()
    counter.register_billing_sink(_StrictSink())
    with pytest.raises(RuntimeError):
        counter.set_api_tokens(agent_name="root", prompt_tokens=1, completed_tokens=1)


def test_bridge_records_into_budget_tracker():
    tracker = budget_module.BudgetTracker(limit_usd=5.0)
    sink = bridge_module.BudgetUsageSink(tracker)
    sink.record_usage(model="gpt-4", prompt_tokens=1000, completion_tokens=500, scope_key="root")

    # LiteLLM 未安装时 estimate_cost 返回 None：不累计金额，但记账本身要成功
    snapshot = sink.snapshot()
    assert snapshot["records"] == 1
    assert tracker.spent_for(budget_module.BudgetScope.AGENT, "root") >= 0


def test_bridge_counts_by_agent_scope():
    tracker = budget_module.BudgetTracker()
    sink = bridge_module.BudgetUsageSink(tracker)
    sink.record_usage(model="gpt-4", prompt_tokens=10, completion_tokens=1, scope_key="agent-a")
    sink.record_usage(model="gpt-4", prompt_tokens=10, completion_tokens=1, scope_key="agent-b")
    assert sink.snapshot()["records"] == 2


def test_bridge_raises_when_budget_policy_is_raise():
    """策略为 RAISE 时超预算要抛 —— 这是调用方显式选择的行为。"""
    tracker = budget_module.BudgetTracker(
        limit_usd=0.0, exceeded_action=budget_module.BudgetExceededAction.RAISE,
    )
    sink = bridge_module.BudgetUsageSink(tracker)

    class _PricedModel:
        """让 estimate_cost 拿到一个非零价格。"""
        pass

    monkeypatched = {"called": False}
    original = budget_module.estimate_cost

    def fake_estimate_cost(model, *, prompt_tokens=0, completion_tokens=0, cached_tokens=0):
        monkeypatched["called"] = True
        return 1.0

    budget_module.estimate_cost = fake_estimate_cost
    try:
        with pytest.raises(budget_module.BudgetExceededError):
            sink.record_usage(model="gpt-4", prompt_tokens=1, completion_tokens=1)
        assert monkeypatched["called"]
    finally:
        budget_module.estimate_cost = original
