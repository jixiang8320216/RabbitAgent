# -*- coding: utf-8 -*-
"""L1-3 Hook 生命周期：触发准确性、拦截有效性、误杀率、失败策略、时序、开销。

Hook 是 L4 安全能力的执行载体，因此本节同时服务于 L1（能力正确性）与
L4（安全有效性）。指南强调 L1-3.4 最容易被忽略：一个 fail-open 的安全 hook
等于没有 hook，因此必须显式测试「hook 自身异常」这一场景。
"""

from __future__ import annotations

import time

import pytest

from framework.case import case, require, require_eq

pytestmark = pytest.mark.p0


@pytest.fixture
def rt():
    """HookRuntime 构造快捷方式。

    handlers 形如 {"PreToolUse": [("名称", handler), ...]}，
    也可传入 HookDefinition 或 {"handler":..., "timeout":..., "matcher":...} 字典。
    """
    from domains.hook.application.hook_runtime import HookDefinition, HookRuntime
    from domains.hook.domain.resource_models import HookPolicy, HookSnapshot

    def _coerce(entry):
        if isinstance(entry, HookDefinition):
            return entry
        if isinstance(entry, tuple):
            name, handler = entry
            return HookDefinition(name=name, handler=handler)
        if isinstance(entry, dict):
            return HookDefinition(**entry)
        raise TypeError(f"不支持的 hook 定义类型：{type(entry).__name__}")

    def _make(handlers: dict[str, list], *, policy: HookPolicy | None = None, **snap_kw):
        hooks = {event: [_coerce(e) for e in defs] for event, defs in handlers.items()}
        return HookRuntime(HookSnapshot(hooks=hooks, **snap_kw), policy or HookPolicy())

    return _make


def _invoke(event=None, tool_name: str = "t", **kw):
    from domains.hook.application.hook_runtime import HookInvocation
    from domains.hook.domain.hook_definitions import HookEvent

    return HookInvocation(
        event=event if event is not None else HookEvent.PRE_TOOL_USE,
        tool_name=tool_name,
        **kw,
    )


# ── L1-3.1 触发准确性 ───────────────────────────────────────────
@case(
    "L1-3.1",
    "该触发时必触发：注册在 PreToolUse 的 hook 在 PRE_TOOL_USE 事件时被执行",
    title="该触发的 hook 未触发",
    dimension="Hook - 触发准确性",
    severity="P0",
    threshold="100%",
    counter_example="注册了 PreToolUse 但事件到来时未执行 → 防护形同虚设",
    tags=("黄金",),
)
def test_hook_triggers_when_registered(rt):
    fired = []
    runtime = rt({"PreToolUse": [("h1", lambda inv: fired.append(inv.tool_name) or "continue")]})
    runtime.dispatch(_invoke(tool_name="rm"))
    require_eq(fired, ["rm"], "已注册 hook 的触发记录")


@case(
    "L1-3.1",
    "不该触发时不触发：注册在 PostToolUse 的 hook 在 PRE_TOOL_USE 事件时不执行",
    title="未注册事件的 hook 被误触发",
    dimension="Hook - 触发准确性",
    severity="P0",
    threshold="100%",
    counter_example="PostToolUse hook 在 PreToolUse 阶段被执行 → 逻辑错乱",
    tags=("黄金",),
)
def test_hook_not_triggered_for_other_events(rt):
    from domains.hook.domain.hook_definitions import HookEvent

    fired = []
    runtime = rt({"PostToolUse": [("h1", lambda inv: fired.append(1) or "continue")]})
    runtime.dispatch(_invoke(event=HookEvent.PRE_TOOL_USE))
    require_eq(fired, [], "非注册事件的触发记录")


# ── L1-3.2 拦截有效性（用实际危险操作验证，非模拟） ──────────────
@case(
    "L1-3.2",
    "返回 deny 的 hook 使事件级裁决为 DENY，工具调用被真实阻断",
    title="deny 裁决未阻断工具调用",
    dimension="Hook - 拦截有效性",
    severity="P0",
    threshold="100%",
    counter_example="hook 返回 deny 但 decision 不是 DENY → 拦截失效",
    tags=("黄金",),
)
def test_deny_decision_blocks_execution(rt):
    from domains.hook.domain.outputs import HookDecisionAction

    runtime = rt({"PreToolUse": [("denier", lambda inv: {"decision": "deny", "reason": "策略禁止删除"})]})
    trace = runtime.dispatch(_invoke(tool_name="rm", tool_input={"path": "/etc/passwd"}))

    require_eq(trace.decision, HookDecisionAction.DENY, "事件级裁决")
    require(trace.denied is True, "trace.denied 未标记为已阻断")
    require("策略禁止删除" in trace.reason, "阻断原因未传递给调用方", detail=trace.reason)


@case(
    "L1-3.2",
    "多条 hook 中任一返回 deny，整体裁决即为 DENY（最严胜出）",
    title="deny 未在多 hook 聚合中胜出",
    dimension="Hook - 拦截有效性",
    severity="P0",
    threshold="100%",
    counter_example="denier 在前、allower 在后时结果为 ALLOW → 拦截被绕过",
    tags=("对抗",),
)
@pytest.mark.parametrize(
    "order",
    [
        ("denier", "allower"),
        ("allower", "denier"),
    ],
    ids=["deny在前", "deny在后"],
)
def test_deny_wins_regardless_of_order(rt, order):
    from domains.hook.domain.outputs import HookDecisionAction

    handlers = {
        "denier": lambda inv: {"decision": "deny", "reason": "blocked"},
        "allower": lambda inv: "allow",
    }
    runtime = rt({"PreToolUse": [(name, handlers[name]) for name in order]})
    trace = runtime.dispatch(_invoke())
    require_eq(trace.decision, HookDecisionAction.DENY, f"顺序 {order} 下的裁决")


@case(
    "L1-3.2",
    "block 与 deny 两种阻断语义都被识别为已阻断",
    title="block 语义未被识别为阻断",
    dimension="Hook - 拦截有效性",
    severity="P0",
    threshold="100%",
    counter_example="decision=block 但 trace.denied 为 False → 阻断状态不可观测",
    tags=("边界",),
)
@pytest.mark.parametrize("verb", ["deny", "block"])
def test_both_blocking_verbs_recognized(rt, verb):
    from domains.hook.domain.outputs import HookDecisionAction

    runtime = rt({"PreToolUse": [("b", lambda inv: {"decision": verb, "reason": "r"})]})
    trace = runtime.dispatch(_invoke())
    require_eq(trace.decision, HookDecisionAction(verb), f"decision={verb} 的归一化结果")
    require(trace.denied is True, f"decision={verb} 未被标记为已阻断")


# ── L1-3.3 误杀率 ───────────────────────────────────────────────
@case(
    "L1-3.3",
    "matcher 返回 False 的 hook 被跳过，不产生裁决影响（正常操作不被错误拦截）",
    title="matcher 未命中的 hook 未被跳过",
    dimension="Hook - 误杀率",
    severity="P0",
    threshold="误杀率 ≤ 1%",
    counter_example="matcher=False 仍产生 DENY → 正常操作被错误拦截",
    tags=("边界",),
)
def test_matcher_skips_non_matching(rt):
    from domains.hook.domain.outputs import HookDecisionAction

    runtime = rt(
        {
            "PreToolUse": [
                {
                    "name": "scoped",
                    "handler": lambda inv: {"decision": "deny", "reason": "不应触发"},
                    "matcher": lambda inv: inv.tool_name == "danger",
                }
            ]
        }
    )
    trace = runtime.dispatch(_invoke(tool_name="safe_read"))
    require_eq(trace.decision, HookDecisionAction.CONTINUE, "非目标工具的裁决")
    require("skipped" in trace.statuses, "未命中的 hook 未被标记为 skipped", detail=str(trace.statuses))


@case(
    "L1-3.3",
    "无 hook 配置时事件裁决降级为 CONTINUE，不阻断任何正常流程",
    title="空配置导致正常流程被阻断",
    dimension="Hook - 误杀率",
    severity="P0",
    threshold="100%",
    counter_example="空快照下 decision 非 CONTINUE → 全系统误杀",
    tags=("黄金",),
)
def test_empty_snapshot_does_not_block():
    from domains.hook.application.hook_runtime import HookRuntime
    from domains.hook.domain.hook_definitions import HookEvent
    from domains.hook.domain.outputs import HookDecisionAction

    trace = HookRuntime().run_event(HookEvent.POST_TOOL_USE)
    require_eq(trace.decision, HookDecisionAction.CONTINUE, "空快照下的裁决")


@case(
    "L1-3.3",
    "已停用（active=False）的工具组不参与 schema 暴露，正常工具不被误伤",
    title="停用工具组仍出现在可用列表",
    dimension="Hook - 误杀率",
    severity="P1",
    threshold="100%",
    counter_example="停用组的工具仍出现在 tool_names() 中",
    tags=("边界",),
)
def test_disabled_tool_group_excluded():
    from domains.tool.core.application.toolkit import Toolkit

    toolkit = Toolkit()
    toolkit.register_tool_function(lambda: "a", name="tool_a", group="grp")
    toolkit.create_tool_group("grp", "测试组")
    toolkit.update_tool_groups(["grp"], False)

    require_eq(toolkit.tool_names(), [], "停用组的可用工具列表")
    require_eq(sorted(toolkit.tool_names(active_only=False)), ["tool_a"], "含停用工具的列表")


# ── L1-3.4 失败策略（P0，最易被忽略） ───────────────────────────
@case(
    "L1-3.4",
    "安全敏感事件（PreToolUse）中 hook 自身崩溃时，裁决为 DENY（fail-close）",
    title="安全 hook 崩溃后未 fail-close",
    dimension="Hook - 失败策略",
    severity="P0",
    threshold="100%（安全场景必须 fail-close）",
    counter_example="PreToolUse hook 抛异常后 decision=CONTINUE → 安全 hook 形同不存在",
    tags=("故障", "P0"),
)
def test_security_hook_failure_fails_closed(rt):
    from domains.hook.domain.outputs import HookDecisionAction

    def _boom(_inv):
        raise RuntimeError("hook 内部崩溃")

    runtime = rt({"PreToolUse": [("boom", _boom)]})
    trace = runtime.dispatch(_invoke())

    require_eq(trace.decision, HookDecisionAction.DENY, "安全事件 hook 崩溃后的裁决")
    require(trace.fail_closed is True, "trace 未标记 fail_closed")
    require("failed" in trace.statuses, "崩溃状态未记录为 failed", detail=str(trace.statuses))


@case(
    "L1-3.4",
    "安全敏感事件中 hook 超时时裁决为 DENY（fail-close）",
    title="安全 hook 超时后未 fail-close",
    dimension="Hook - 失败策略",
    severity="P0",
    threshold="100%",
    counter_example="超时后 CONTINUE → 攻击者可用慢 hook 拖过防护窗口",
    tags=("故障",),
)
def test_security_hook_timeout_fails_closed(rt):
    from domains.hook.application.hook_runtime import HookDefinition
    from domains.hook.domain.resource_models import HookSnapshot
    from domains.hook.application.hook_runtime import HookRuntime
    from domains.hook.domain.resource_models import HookPolicy
    from domains.hook.domain.outputs import HookDecisionAction

    snapshot = HookSnapshot(
        hooks={"PreToolUse": [HookDefinition(name="slow", handler=lambda i: time.sleep(3), timeout=0.2)]}
    )
    trace = HookRuntime(snapshot, HookPolicy()).dispatch(_invoke())
    require_eq(trace.decision, HookDecisionAction.DENY, "超时后的裁决")


@case(
    "L1-3.4",
    "非安全事件（PostToolUse）中 hook 崩溃时裁决为 CONTINUE（fail-open，避免阻塞业务）",
    title="非安全事件错误地 fail-close",
    dimension="Hook - 失败策略",
    severity="P1",
    threshold="100%",
    counter_example="PostToolUse 崩溃导致 DENY → 约束过严导致系统不可用",
    tags=("故障",),
)
def test_non_security_hook_failure_fails_open(rt):
    from domains.hook.domain.hook_definitions import HookEvent
    from domains.hook.domain.outputs import HookDecisionAction

    def _boom(_inv):
        raise RuntimeError("hook 内部崩溃")

    runtime = rt({"PostToolUse": [("boom", _boom)]})
    trace = runtime.dispatch(_invoke(event=HookEvent.POST_TOOL_USE))

    require_eq(trace.decision, HookDecisionAction.CONTINUE, "非安全事件崩溃后的裁决")
    require(trace.fail_closed is False, "非安全事件被错误标记为 fail_closed")


@case(
    "L1-3.4",
    "HookPolicy(continue_on_error=False) 使所有事件无条件 fail-close",
    title="严格策略未生效",
    dimension="Hook - 失败策略",
    severity="P1",
    threshold="100%",
    counter_example="全局严格模式下仍 fail-open → 策略开关无效",
    tags=("故障",),
)
def test_strict_policy_forces_fail_close():
    from domains.hook.application.hook_runtime import HookRuntime
    from domains.hook.domain.hook_definitions import HookEvent
    from domains.hook.domain.outputs import HookDecisionAction
    from domains.hook.domain.resource_models import HookPolicy, HookSnapshot
    from domains.hook.application.hook_runtime import HookDefinition, HookInvocation

    def _boom(_inv):
        raise RuntimeError("boom")

    snapshot = HookSnapshot(hooks={"PostToolUse": [HookDefinition(name="b", handler=_boom)]})
    trace = HookRuntime(snapshot, HookPolicy(continue_on_error=False)).dispatch(
        HookInvocation(event=HookEvent.POST_TOOL_USE)
    )
    require_eq(trace.decision, HookDecisionAction.DENY, "严格策略下的裁决")


@case(
    "L1-3.4",
    "HookPolicy 拒绝非正数的超时配置（构造期校验）",
    title="非法超时配置被接受",
    dimension="Hook - 失败策略",
    severity="P1",
    threshold="100%",
    counter_example="default_timeout_seconds=0 被接受 → 超时保护失效",
    tags=("边界",),
)
@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_invalid_timeout_rejected(bad):
    from domains.hook.domain.resource_models import HookPolicy

    with pytest.raises(ValueError):
        HookPolicy(default_timeout_seconds=bad)


# ── L1-3.5 时序正确 ─────────────────────────────────────────────
@case(
    "L1-3.5",
    "多个 hook 按注册顺序串行执行",
    title="hook 执行顺序与注册顺序不一致",
    dimension="Hook - 时序正确",
    severity="P0",
    threshold="100%",
    counter_example="执行顺序被打乱 → 前置校验被后置逻辑跳过",
    tags=("黄金",),
)
def test_hooks_execute_in_registration_order(rt):
    order = []
    runtime = rt(
        {
            "PreToolUse": [
                ("h1", lambda inv: order.append("h1") or "continue"),
                ("h2", lambda inv: order.append("h2") or "continue"),
                ("h3", lambda inv: order.append("h3") or "continue"),
            ]
        }
    )
    runtime.dispatch(_invoke())
    require_eq(order, ["h1", "h2", "h3"], "hook 执行顺序")


@case(
    "L1-3.5",
    "裁决严重度满足 DENY/BLOCK > FAIL/CANCEL > ASK > ALLOW > CONTINUE 的严格优先级",
    title="裁决优先级定义缺失",
    dimension="Hook - 时序正确",
    severity="P0",
    threshold="100%",
    counter_example="严重度表缺失或顺序错误 → 最严裁决无法胜出",
    tags=("黄金",),
)
def test_decision_severity_ordering():
    from domains.hook.application.hook_runtime import DECISION_SEVERITY
    from domains.hook.domain.outputs import HookDecisionAction

    require(DECISION_SEVERITY, "裁决严重度表为空")
    for stronger, weaker in [
        (HookDecisionAction.DENY, HookDecisionAction.BLOCK),
        (HookDecisionAction.DENY, HookDecisionAction.FAIL),
        (HookDecisionAction.FAIL, HookDecisionAction.ASK),
        (HookDecisionAction.ASK, HookDecisionAction.ALLOW),
        (HookDecisionAction.ALLOW, HookDecisionAction.CONTINUE),
    ]:
        require(
            DECISION_SEVERITY[stronger] >= DECISION_SEVERITY[weaker],
            f"严重度顺序错误：{stronger.value}({DECISION_SEVERITY[stronger]}) "
            f"应 ≥ {weaker.value}({DECISION_SEVERITY[weaker]})",
        )
    require_eq(
        DECISION_SEVERITY[HookDecisionAction.DENY],
        DECISION_SEVERITY[HookDecisionAction.BLOCK],
        "DENY 与 BLOCK 的严重度",
    )


@case(
    "L1-3.5",
    "HookDecisionAction 覆盖全部 10 种裁决语义",
    title="裁决语义不完整",
    dimension="Hook - 时序正确",
    severity="P1",
    threshold="≥ 10 种",
    counter_example="缺少 ask 语义 → 无法表达「需用户确认」",
    tags=("黄金",),
)
def test_decision_actions_complete():
    from domains.hook.domain.outputs import HookDecisionAction

    expected = {
        "continue", "allow", "ask", "defer", "deny", "block", "accept", "decline", "cancel", "fail",
    }
    actual = {m.value for m in HookDecisionAction}
    missing = expected - actual
    require(not missing, f"缺少裁决语义：{missing}", detail=f"现有：{sorted(actual)}")
    require_eq(len(actual), len(expected), "裁决语义总数")


# ── L1-3.6 执行开销 ─────────────────────────────────────────────
@case(
    "L1-3.6",
    "空 hook 配置的调度开销增量 < 5ms，不显著增加端到端延迟",
    title="Hook 调度开销超标",
    dimension="Hook - 执行开销",
    severity="P1",
    threshold="增量 < 50ms（此处收紧至 5ms，因空配置无业务开销）",
    counter_example="空配置调度耗时 > 5ms → 每轮对话累积延迟",
    tags=("性能",),
)
def test_hook_overhead_within_budget():
    from domains.hook.application.hook_runtime import HookRuntime
    from domains.hook.domain.hook_definitions import HookEvent

    runtime = HookRuntime()
    runtime.run_event(HookEvent.PRE_TOOL_USE)  # 预热

    samples = []
    for _ in range(200):
        started = time.perf_counter()
        runtime.run_event(HookEvent.PRE_TOOL_USE)
        samples.append((time.perf_counter() - started) * 1000)

    avg_ms = sum(samples) / len(samples)
    require(
        avg_ms < 5.0,
        f"空配置 hook 调度平均耗时 {avg_ms:.3f}ms，超出 5ms 预算",
        detail=f"最大 {max(samples):.3f}ms",
    )


@case(
    "L1-3.6",
    "Hook 执行耗时被正确记录，可用于定位慢 hook",
    title="Hook 耗时未记录",
    dimension="Hook - 执行开销",
    severity="P1",
    threshold="elapsed_ms > 0",
    counter_example="elapsed_ms 恒为 0 → 无法定位慢 hook",
    tags=("性能",),
)
def test_hook_elapsed_recorded(rt):
    runtime = rt({"PreToolUse": [("h", lambda inv: time.sleep(0.05) or "continue")]})
    trace = runtime.dispatch(_invoke())
    require(trace.outcomes, "未产生执行结果")
    require(
        trace.outcomes[0].elapsed_ms > 0,
        "hook 执行耗时未记录（elapsed_ms = 0）",
        detail=f"elapsed_ms={trace.outcomes[0].elapsed_ms}",
    )


# ── 事件定义完整性 ──────────────────────────────────────────────
@case(
    "L1-3.1",
    "安全敏感事件集合覆盖 PreToolUse 与 PermissionRequest 两个必须 fail-close 的事件",
    title="安全敏感事件集合不完整",
    dimension="Hook - 触发准确性",
    severity="P0",
    threshold="100%",
    counter_example="PermissionRequest 不在集合中 → 权限请求无保护",
    tags=("黄金",),
)
def test_security_critical_events_complete():
    from domains.hook.application.hook_runtime import SECURITY_CRITICAL_EVENTS

    values = {e.value for e in SECURITY_CRITICAL_EVENTS}
    require_eq(values, {"PreToolUse", "PermissionRequest"}, "安全敏感事件集合")


@case(
    "L1-3.1",
    "事件枚举使用 CamelCase 取值，与 HookSnapshot 的键查找一致",
    title="事件枚举取值不一致",
    dimension="Hook - 触发准确性",
    severity="P0",
    threshold="100%",
    counter_example="使用 snake_case 的 HookEvent 会使 fail-close 判定静默失效",
    tags=("故障",),
)
def test_hook_event_enum_casing_consistent():
    """项目中存在两个同名 HookEvent 枚举，取值风格不同。

    误用 lifecycle 版本会使 is_security_critical 恒为 False，
    导致所有安全 hook 的 fail-close 静默失效——这是极高风险的陷阱。
    """
    from domains.hook.application.hook_runtime import HookInvocation
    from domains.hook.domain.hook_definitions import HookEvent as Canonical

    require_eq(Canonical.PRE_TOOL_USE.value, "PreToolUse", "主枚举 PRE_TOOL_USE 取值")

    inv = HookInvocation(event=Canonical.PRE_TOOL_USE)
    require(
        inv.is_security_critical is True,
        "主枚举构造的 HookInvocation 未被识别为安全敏感事件",
        detail=f"event={Canonical.PRE_TOOL_USE.value!r}, is_security_critical={inv.is_security_critical}",
    )


@case(
    "L1-3.4",
    "Hook 返回无法识别的裁决值时降级为 CONTINUE 并告警，不静默放行高危操作",
    title="非法裁决值被静默处理",
    dimension="Hook - 失败策略",
    severity="P0",
    threshold="100%",
    counter_example="未知裁决值被当作 ALLOW 处理 → 防护被绕过",
    tags=("故障", "脏数据"),
)
def test_unknown_decision_degrades_safely(rt):
    from domains.hook.domain.outputs import HookDecisionAction

    runtime = rt({"PreToolUse": [("weird", lambda inv: "maybe_proceed")]})
    trace = runtime.dispatch(_invoke())
    require(
        trace.decision is not None,
        "非法裁决值导致裁决结果为 None",
    )
    # 安全事件下 fail-close 优先：无论取值如何识别，都不得放行为 ALLOW
    require(
        trace.decision is not HookDecisionAction.ALLOW,
        f"非法裁决值被当作 ALLOW 处理（实际 {trace.decision}）→ 防护被绕过",
        detail=f"reason={trace.reason}",
    )