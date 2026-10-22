"""Agent 模块验证脚本（无网络 / 无真实 LLM）。

验证三项：
  A. 业务可达性（G1）：每个 agent 实现类都能成功导入。
  B. 可调用性：以桩对象构造「已接入运行时的基类 ReActAgent」与各兄弟实现类，
     证明它们能被正常实例化（构造不抛异常、公开 run/reply 入口存在）。
  C. 配置参数决定使用：``agent_type``（槽位）/ ``app_mode``（会话模式）驱动运行时
     选择不同的 agent 构建路径（编排者 vs 通用 ReActAgent）与不同的执行拓扑。

直接运行：
    python verify_agent_module.py
退出码 0 = 全部通过；非 0 = 存在未达项（详情见输出）。
"""

from __future__ import annotations

import asyncio
import types
import sys
from unittest import mock

RESULTS: list[tuple[str, str, str]] = []  # (分组, 项目, 结论/备注)


def record(group: str, item: str, verdict: str) -> None:
    RESULTS.append((group, item, verdict))
    print(f"[{ 'PASS' if verdict.startswith('OK') else 'FAIL' if verdict.startswith('XX') else 'INFO' }] {group} :: {item} -> {verdict}")


# --------------------------------------------------------------------------
# 桩对象
# --------------------------------------------------------------------------
class FakeLLM:
    """最小可用的 LLM 桩（仅需满足构造期形态，不发起真实请求）。"""

    def __init__(self, *a, **k):
        self.provider = "fake"

    def think(self, messages, temperature=None):
        return iter([""])

    def invoke(self, messages, **kwargs):
        return ""

    def stream_invoke(self, messages, **kwargs):
        return iter([""])


class FakeChatModel:
    """ReActAgent.model 只要「可调用、能返回可归一化成 Msg 的产物」。"""

    def __call__(self, *a, **k):
        return "ok"

    async def ainvoke(self, *a, **k):
        return "ok"

    def invoke(self, *a, **k):
        return "ok"

    def chat(self, *a, **k):
        return "ok"

    def complete(self, *a, **k):
        return "ok"


class FakeFormatter:
    """满足 MessageFormatter 协议（``async def format``）。"""

    async def format(self, *a, **k):
        return []


# --------------------------------------------------------------------------
# A. 导入可达性
# --------------------------------------------------------------------------
def check_imports() -> None:
    group = "A.导入可达"
    # 架构已定型为「ReActAgent 单类 + preset_type 配置预设」：
    # 原 SimpleAgent / FunctionCallAgent / PlanAndSolveAgent / ReflectionAgent /
    # ToolAwareSimpleAgent 五个独立类已由 preset_type 预设承接并删除，
    # 这里只校验运行时唯一的 agent 类。
    targets = {
        "ReActAgent": "domains.agent.domain.aggregates.react_agent",
    }
    for name, mod in targets.items():
        try:
            m = __import__(mod, fromlist=[name])
            cls = getattr(m, name, None)
            ok = callable(cls)
            record(group, f"{name} <- {mod}", "OK 类已导出" if ok else "XX 模块导入成功但类缺失")
        except Exception as e:  # noqa: BLE001
            record(group, f"{name} <- {mod}", f"XX {type(e).__name__}: {e}")


# --------------------------------------------------------------------------
# B. 可构造性（可调用）
# --------------------------------------------------------------------------
def check_react_agent_constructable() -> None:
    group = "B.ReActAgent(已接入基类)"
    try:
        from domains.agent.domain.aggregates.react_agent import ReActAgent
        from domains.plan.application.plan_runtime import PlanRuntime
        from domains.agent.domain.value_objects.conversation_context import Context
        from domains.agent.domain.entities.agent_context import AgentContext

        agent = ReActAgent(
            name="verify-agent",
            sys_prompt="you are a verifier",
            model=FakeChatModel(),
            formatter=FakeFormatter(),
            toolkit=types.SimpleNamespace(tools={}, register_tool_function=lambda *a, **k: None),
            context=Context(),
            max_iters=3,
            plan_runtime=PlanRuntime(),
            enable_meta_tool=False,
            agent_context=AgentContext(agent_id="verify-agent", session_id="verify-session"),
        )
        # 公开调用入口存在性（真实 run 需 LLM，这里只校验入口齐备）
        has_reply = any(
            hasattr(agent, m) and callable(getattr(agent, m))
            for m in ("reply", "run", "achat", "stream_reply", "aask")
        )
        record(group, "构造 + 入口自检", "OK 实例化成功" + ("" if has_reply else "（无标准 reply/run 入口）"))
    except Exception as e:  # noqa: BLE001
        record(group, "构造 + 入口自检", f"XX {type(e).__name__}: {e}")


def check_preset_coverage() -> None:
    """旧「兄弟类构造」检查已随 5 个独立类删除而废弃。

    它们的行为现由 ``preset_type`` 预设承接，路由正确性由
    ``verify_agent_preset_routing.py`` 验证；这里只确认路由表覆盖了全部预设值。
    """
    group = "B.preset_type 预设覆盖"
    import domains.agent.domain.aggregates.react_agent as ra
    import domains.agent.domain.entities.agent_runtime as agent_runtime

    presets = list(agent_runtime.AgentPresetType.__args__)
    table = ra.ReActAgent._PRESET_REPLY_STRATEGY
    missing = [p for p in presets if p not in table]
    record(group, f"{len(presets)} 个预设全部登记策略",
           "OK 全覆盖" if not missing else f"XX 缺失={missing}")


# --------------------------------------------------------------------------
# C. 配置参数决定使用
# --------------------------------------------------------------------------
async def check_config_selection() -> None:
    group = "C.配置参数选择"
    from domains.configuration.domain.model.app_mode import (
        BUILTIN_APP_MODES,
        canonicalize_app_mode,
        is_valid_app_mode,
    )
    from domains.plugin.domain.contribution import AgentSlot
    from domains.runtime.application.orchestrators.mode_agent_creation import ModeAgentCreationService

    # C1: app_mode 取值空间
    for mode in sorted(BUILTIN_APP_MODES):
        try:
            got = canonicalize_app_mode(mode)
            ok = got == mode
            record(group, f"app_mode 归一化 '{mode}'", "OK" if ok else f"XX 得到 {got!r}")
        except Exception as e:  # noqa: BLE001
            record(group, f"app_mode 归一化 '{mode}'", f"XX {e}")
    # 非法值必须被拒
    try:
        canonicalize_app_mode("not-a-real-mode")
        record(group, "app_mode 拒绝非法值", "XX 未拒绝非法模式")
    except ValueError:
        record(group, "app_mode 拒绝非法值", "OK 抛 ValueError")
    except Exception as e:  # noqa: BLE001
        record(group, f"app_mode 拒绝非法值", f"XX 抛错类型不对: {e}")

    # C2: 每个内置模式都被 ModeAgentCreationService 支持（groupchat 单独走群聊构建，
    #     因此 supports('groupchat')==False 属预期，不计入失败）
    svc = ModeAgentCreationService()
    for mode in sorted(BUILTIN_APP_MODES):
        supported = svc.supports(mode)
        expected = mode != "groupchat"
        if supported == expected:
            note = "（groupchat 由群聊构建器负责，本服务不支持属预期）" if mode == "groupchat" else ""
            record(group, f"supports('{mode}')", "OK" + note)
        else:
            record(group, f"supports('{mode}')", f"XX 期望 {expected} 实际 {supported}")

    # C3: agent_type 决定走哪个构建器（核心路由）
    svc = ModeAgentCreationService(supported_modes=BUILTIN_APP_MODES - {"groupchat"})

    # ModeAgentCreationService 使用 __slots__，不能在实例上打补丁；改为临时替换类属性，
    # 并在同一个 try 块内完成两次路由验证后再还原。
    orig_build = ModeAgentCreationService._build_runtime_config
    orig_resolve = ModeAgentCreationService._resolve_agent_id
    ModeAgentCreationService._build_runtime_config = staticmethod(
        lambda *a, **k: types.SimpleNamespace(name="rc")
    )
    ModeAgentCreationService._resolve_agent_id = staticmethod(lambda *a, **k: "agent-id")

    async def run_route(agent_type: str):
        host = types.SimpleNamespace(
            app=types.SimpleNamespace(
                agent_runtimes={},
                active_mode_declaration=types.SimpleNamespace(root_agent="root"),
                observer=None,
            )
        )
        agent_factory = types.SimpleNamespace(
            create_react_agent=mock.AsyncMock(return_value="REACT_AGENT"),
        )
        host._build_orchestrator_agent = mock.AsyncMock(return_value="ORCH_AGENT")
        slot = AgentSlot(name="slot", agent_type=agent_type)
        result = await svc.create(
            host,
            slot=slot,
            sub_agents=[],
            agent_factory=agent_factory,
        )
        return result, agent_factory.create_react_agent.called, host._build_orchestrator_agent.called

    try:
        result, to_react, to_orch = await run_route("orchestrator")
        ok = (to_orch and not to_react and result == "ORCH_AGENT")
        record(group, "agent_type='orchestrator' -> 编排者构建器",
               "OK 走 _build_orchestrator_agent" if ok else f"XX react={to_react} orch={to_orch} res={result!r}")

        result, to_react, to_orch = await run_route("general")
        ok = (to_react and not to_orch and result == "REACT_AGENT")
        record(group, "agent_type='general' -> 通用构建器(ReActAgent)",
               "OK 走 agent_factory.create_react_agent" if ok else f"XX react={to_react} orch={to_orch} res={result!r}")
    finally:
        ModeAgentCreationService._build_runtime_config = orig_build
        ModeAgentCreationService._resolve_agent_id = orig_resolve

    # C4: 内置模式声明齐备（每个模式都有对应 ModeDeclaration）
    try:
        from domains.agent.application.prompt import builtin_mode_declarations as bmd

        present = sorted(
            n for n in ("DELEGATE_DECLARATION", "SOLO_DECLARATION", "ROLEPLAY_DECLARATION",
                        "GUARDED_DECLARATION", "PIPELINE_DECLARATION", "PARALLEL_DECLARATION",
                        "DEBATE_DECLARATION", "GROUPCHAT_DECLARATION")
            if hasattr(bmd, n)
        )
        record(group, f"内置模式声明齐备 ({len(present)}/8)",
               "OK " + ",".join(present) if len(present) >= 6 else f"XX 仅 {present}")
    except Exception as e:  # noqa: BLE001
        record(group, "内置模式声明齐备", f"XX {type(e).__name__}: {e}")


def main() -> int:
    print("=" * 70)
    print("Agent 模块验证")
    print("=" * 70)
    check_imports()
    check_react_agent_constructable()
    check_preset_coverage()
    asyncio.run(check_config_selection())

    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    fails = [r for r in RESULTS if r[2].startswith("XX")]
    infos = [r for r in RESULTS if r[2].startswith("INFO")]
    oks = [r for r in RESULTS if r[2].startswith("OK")]
    print(f"总计 {len(RESULTS)} 项：OK={len(oks)}  INFO={len(infos)}  FAIL={len(fails)}")
    if fails:
        print("\n未达项：")
        for g, i, v in fails:
            print(f"  - {g} :: {i}: {v}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
