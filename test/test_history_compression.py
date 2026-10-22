"""历史消息自动压缩的用例。

覆盖三个此前缺口：`compressionThreshold` 的执行者、`AgentCompressionState`
的生命周期、以及「不能切开工具调用序列」这条硬约束。
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import domains.agent.domain.value_objects.conversation_context as conversation_context  # noqa: E402
import domains.agent.domain.value_objects.message as message_module  # noqa: E402
import domains.context.application.history_compression as compression_service  # noqa: E402
import domains.context.domain.history_compression_policy as policy  # noqa: E402


def _text(index: int) -> str:
    return f"第{index}轮：用户提出的约束、已确认的事实和已经做过的操作都需要被保留下来。"


def _build_context(rounds: int = 20) -> conversation_context.Context:
    context = conversation_context.Context()
    for index in range(rounds):
        context._entries.append(
            conversation_context.ContextEntry(
                message=message_module.Msg("u", _text(index), "user")
            )
        )
        context._entries.append(
            conversation_context.ContextEntry(
                message=message_module.Msg("a", _text(index), "assistant")
            )
        )
    return context


# ------------------------------------------------------------------ 策略层

def test_token_estimate_is_monotonic():
    short = policy.estimate_tokens("短")
    long = policy.estimate_tokens("短" * 100)
    assert short < long
    # CJK 按字符计:
    assert policy.estimate_tokens("你好") == 2


def test_window_selection_skips_head_system_messages():
    messages = [
        message_module.Msg("sys", "系统人设与约束必须保留。", "system"),
        *(message_module.Msg("u", _text(i), "user") for i in range(10)),
    ]
    plan = policy.select_compression_window(messages, threshold=10, keep_recent=2)
    assert plan.start == 1, "system 消息不该进入压缩区"
    assert plan.end == len(messages) - 2


def test_window_selection_never_splits_tool_sequence():
    """工具序列不能被切开 —— 否则模型侧会报 tool_call_id 无对应结果。"""
    assistant = message_module.Msg(
        "a",
        [{"type": "tool_use", "id": "call-1", "name": "search", "input": {}}],
        "assistant",
    )
    tool_result = message_module.Msg(
        "tool",
        # ToolResultBlock 只接受 id / name / output / type
        [{"type": "tool_result", "id": "call-1", "name": "search", "output": "结果"}],
        "assistant",
    )
    messages = [
        message_module.Msg("u", _text(i), "user") for i in range(3)
    ] + [assistant, tool_result] + [
        message_module.Msg("u", _text(i), "user") for i in range(6)
    ]

    plan = policy.select_compression_window(messages, threshold=1, keep_recent=1)
    window = messages[plan.start:plan.end]

    # 序列里的两条消息要么同时在窗口里，要么都不在
    assert (assistant in window) == (tool_result in window)


def test_extractive_summary_shrinks_within_budget():
    """摘要按预算半量压缩：字符预算给足时它不会自己变短（服务层另有防呆）。

    这条用例验证的是「给了预算，摘要就一定不超预算」，能否真省 token 则由
    :class:`HistoryCompressionService` 的 no-reduction 守卫兜底。
    """
    messages = [message_module.Msg("u", _text(i), "user") for i in range(5)]
    window_tokens = policy.messages_tokens(messages)
    summary = policy.extractive_summary(messages, max_chars=window_tokens // 2)
    assert summary.startswith("- user:")
    assert len(summary) <= window_tokens // 2
    # 5 条变 5 行：行数一样但每行被预算截断，总长度必须下降
    assert policy.estimate_tokens(summary) < window_tokens


# ------------------------------------------------------------------ 执行者层

def test_service_compresses_and_records_state():
    context = _build_context()
    before_tokens = context.current_tokens
    service = compression_service.HistoryCompressionService(threshold=100, cooldown_messages=0)

    report = asyncio.run(service.maybe_compress(context, agent_id="root"))

    assert report.performed
    assert report.tokens_after < report.tokens_before < before_tokens + 1
    assert report.messages_after < report.messages_before
    # 压缩门必须被注销，否则后续子 agent spawn 会一直等超时
    assert len(service.registry) == 0
    assert context.context[0].role == "system" or "压缩摘要" in str(context.context[0].content)


def test_service_skips_below_threshold():
    context = conversation_context.Context()
    context._entries.append(
        conversation_context.ContextEntry(
            message=message_module.Msg("u", "很短的一句话", "user")
        )
    )
    service = compression_service.HistoryCompressionService(threshold=100000, cooldown_messages=0)
    report = asyncio.run(service.maybe_compress(context, agent_id="root"))
    assert not report.performed
    assert "threshold" in report.reason


def test_service_refuses_no_op_compression():
    """摘要比原文还长时不写回：避免出现「压了反而更胖」的假优化。"""
    context = _build_context(rounds=4)
    service = compression_service.HistoryCompressionService(threshold=1, cooldown_messages=0)

    async def verbose_summarizer(_text_window: str) -> str:
        return "废话" * 500

    service._summarizer = verbose_summarizer
    report = asyncio.run(service.maybe_compress(context, agent_id="root"))
    assert not report.performed
    assert "no reduction" in report.reason


def test_model_summarizer_used_when_available():
    context = _build_context()
    service = compression_service.HistoryCompressionService(threshold=100, cooldown_messages=0)

    async def summarizer(_window: str) -> str:
        return "压缩后的要点：A、B、C"

    service._summarizer = summarizer
    report = asyncio.run(service.maybe_compress(context, agent_id="root"))
    assert report.performed
    assert report.strategy == "model"
    assert any("压缩后的要点" in str(msg.content) for msg in context.context)


def test_falls_back_to_extractive_when_model_fails():
    context = _build_context()
    service = compression_service.HistoryCompressionService(threshold=100, cooldown_messages=0)

    async def broken(_window: str) -> str:
        raise RuntimeError("model unavailable")

    service._summarizer = broken
    report = asyncio.run(service.maybe_compress(context, agent_id="root"))
    assert report.performed
    assert report.strategy == "extractive"


def test_cooldown_prevents_compressing_every_turn():
    context = _build_context()
    service = compression_service.HistoryCompressionService(threshold=100, cooldown_messages=3)

    reports = [asyncio.run(service.maybe_compress(context, agent_id="root")) for _ in range(3)]
    assert [r.performed for r in reports] == [False, False, False]


def test_compression_state_lifecycle_tracks_rounds():
    import domains.agent.domain.entities.agent_compression_state as state_module

    state = state_module.AgentCompressionState("root")
    run_id = state.begin()
    assert state.is_compressing and run_id

    state.finish(strategy="extractive", messages_before=10, messages_after=4,
                 tokens_before=500, tokens_after=200)
    assert not state.is_compressing
    assert state.compression_run_id is None
    assert state.compression_rounds == 1
    assert state.reduced_tokens == 300

    state.fail("boom")
    assert not state.is_compressing, "失败也必须清掉正在压缩的标记"


def test_replace_window_reports_removed_count():
    context = _build_context(rounds=3)
    total_before = len(context.context)
    replacement = message_module.Msg("system", "摘要", "system")
    removed = context.replace_window(1, 3, replacement, "compressed")
    assert removed == 2
    assert len(context.context) == total_before - 1
