"""Agent 模块「单类 + 配置预设」架构验证。

结论先行（架构选型）：
    **方案 B —— 全部集成进 ReActAgent，由 ``preset_type`` 配置参数选择行为。**

判据（均来自代码自身的既有约定，非本次新增臆断）：
    1. ``domains/agent/domain/entities/agent_runtime.py`` 里 ``preset_type:
       AgentPresetType`` 的注释原文是「**预设类型（替代6个独立类）**」；
    2. ``AgentPresetType = Literal["react","simple","plan_solve","reflection",
       "function_call","tool_aware"]`` 正好是那 6 个类；
    3. ``tool_call_mode`` 的注释原文是「原SimpleAgent="text_parse"、
       FunctionCallAgent="native"」；
    4. ``runtime_composition._instantiate_agent`` 从头到尾只构造 ``ReActAgent``；
    5. 兄弟五类属旧 ``Agent(ABC)`` 框架（``llm``/``system_promot``/``config``），
       接口不兼容且抽象 ``reply`` 未实现，从未被运行时引用。

本脚本验证从源头 ``_instantiate_agent`` 到 ``ReActAgent._reply_impl`` 的完整链路：
    AgentRuntime.preset_type
        -> _instantiate_agent(透传)
        -> ReActAgent.__init__(preset_type=...)
        -> _resolve_reply_strategy()
        -> _reply_reflection / _reply_plan_solve / _reply_text_parse / ReAct 主循环
"""

from __future__ import annotations

import ast
import asyncio
import collections
import inspect
import pathlib
import types

RESULTS: list[tuple[str, str, str, str]] = []


def record(group: str, item: str, status: str, detail: str = "") -> None:
    RESULTS.append((group, item, status, detail))
    print(f"  [{status:<4}] {group} :: {item}" + (f"  -> {detail}" if detail else ""))


# ---------------------------------------------------------------- 桩件


class FakeObserver:
    async def on_text_delta(self, *a, **k) -> None:
        return None

    async def on_tool_call_streaming(self, *a, **k) -> None:
        return None

    async def on_text_stream_end(self, *a, **k) -> None:
        return None


class FakeContext:
    """最小 ``conversation_context.Context`` 替身（只用到 add / get_messages）。"""

    def __init__(self) -> None:
        self.messages: list[object] = []

    async def add(self, msg) -> None:
        self.messages.append(msg)

    async def get_messages(self) -> list[object]:
        return list(self.messages)


class FakeFormatter:
    async def format(self, *, msgs) -> list[dict]:
        return [{"role": getattr(m, "role", "user"),
                 "content": getattr(m, "content", "")} for m in msgs]


class FakeToolkit:
    def get_json_schemas(self) -> list[dict]:
        return []


class FakeModel:
    """按序吐出预设文本，走真实的 ``collect_model_turn`` 收集链路。"""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0
        self.observer = FakeObserver()

    async def __call__(self, messages, tools=None, tool_choice=None, **kwargs):
        text = self._texts.pop(0) if self._texts else ""
        self.calls += 1
        return self._stream(text)

    async def _stream(self, text: str):
        yield types.SimpleNamespace(
            content=text, finish_reason="stop", stop_details=None,
        )


def make_agent(**overrides):
    """绕过 __init__ 直接装配一个 ReActAgent（只测策略逻辑，不测装配）。"""
    import domains.agent.domain.aggregates.react_agent as ra

    agent = object.__new__(ra.ReActAgent)
    agent.name = "verify-agent"
    agent._sys_prompt = "you are a verifier"
    agent.context = FakeContext()
    agent.formatter = FakeFormatter()
    agent.toolkit = FakeToolkit()
    agent.max_iters = 3
    agent.plan_runtime = None
    agent.preset_type = "react"
    agent.tool_call_mode = "native"
    agent.reflection_max_iterations = 3
    agent.reflection_prompts = ra._DEFAULT_REFLECTION_PROMPTS
    agent.plan_prompts = ra._DEFAULT_PLANNER_PROMPTS
    agent._reflection_explicitly_configured = False
    agent._plan_prompts_explicitly_configured = False
    agent._hooks = {ht: collections.OrderedDict() for ht in ra.ReActAgent.HOOK_TYPES}
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


# ---------------------------------------------------------------- 验证


def verify_routing_table() -> None:
    import domains.agent.domain.entities.agent_runtime as agent_runtime
    import domains.agent.domain.aggregates.react_agent as ra

    group = "A. 路由表完整性"
    presets = list(agent_runtime.AgentPresetType.__args__)
    record(group, "AgentPresetType 取值", "INFO", ", ".join(presets))

    table = ra.ReActAgent._PRESET_REPLY_STRATEGY
    missing = [p for p in presets if p not in table]
    record(group, "6 个预设全部登记策略",
           "OK" if not missing else "FAIL",
           f"缺失={missing}" if missing else "全覆盖")

    import domains.agent.domain.value_objects.message as message
    msg = message.Msg("verify-agent", "hello", "user")

    expected = {
        "react": "react",
        "function_call": "react",
        "tool_aware": "react",
        "simple": "text_parse",
        "plan_solve": "plan_solve",
        "reflection": "reflection",
    }
    for preset, want in expected.items():
        agent = make_agent(preset_type=preset)
        got = ra.ReActAgent._resolve_reply_strategy(agent, msg)
        record(group, f"preset_type='{preset}' -> {want}",
               "OK" if got == want else "FAIL", f"实得={got}")

    # tool_call_mode 在 react 家族内可单独切到文本解析
    agent = make_agent(preset_type="react", tool_call_mode="text_parse")
    got = ra.ReActAgent._resolve_reply_strategy(agent, msg)
    record(group, "tool_call_mode='text_parse' -> text_parse",
           "OK" if got == "text_parse" else "FAIL", f"实得={got}")

    # 未知预设安全降级
    agent = make_agent(preset_type="no_such_preset")
    got = ra.ReActAgent._resolve_reply_strategy(agent, msg)
    record(group, "未知 preset 安全降级为 react",
           "OK" if got == "react" else "FAIL", f"实得={got}")

    # 旧启发式向后兼容
    agent = make_agent(
        preset_type="react", tool_call_mode="native",
        max_iters=-1, _reflection_explicitly_configured=True,
    )
    got = ra.ReActAgent._resolve_reply_strategy(agent, msg)
    record(group, "旧启发式 反思(max_iters<0) 仍生效",
           "OK" if got == "reflection" else "FAIL", f"实得={got}")

    agent = make_agent(
        preset_type="react", tool_call_mode="native", plan_runtime=None,
        _plan_prompts_explicitly_configured=True,
    )
    got = ra.ReActAgent._resolve_reply_strategy(
        agent, message.Msg("verify-agent", "__plan_solve__ do it", "user"),
    )
    record(group, "旧启发式 规划(__plan_solve__) 仍生效",
           "OK" if got == "plan_solve" else "FAIL", f"实得={got}")


def verify_constructor_wiring() -> None:
    group = "B. 构造字段正确性"
    import domains.agent.domain.aggregates.react_agent as ra

    # 用 AST 判定而不是文本匹配：源码注释里也会提到这段历史写法，
    # 文本匹配会把注释误判为缺陷。
    tree = ast.parse(inspect.getsource(ra.ReActAgent.__init__).lstrip())
    clobbered = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "plan_runtime"
                and "plan_prompts" in ast.dump(node.value)
            ):
                clobbered = True
    record(group, "__init__ 不再用 plan_prompts 覆盖 plan_runtime",
           "FAIL" if clobbered else "OK",
           "仍存在覆盖赋值" if clobbered else "")

    assigns_plan_prompts = any(
        isinstance(t, ast.Attribute) and t.attr == "plan_prompts"
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets
    )
    record(group, "__init__ 正确设置 self.plan_prompts",
           "OK" if assigns_plan_prompts else "FAIL")
    record(group, "__init__ 接收 preset_type 参数",
           "OK" if "preset_type" in inspect.signature(ra.ReActAgent.__init__).parameters
           else "FAIL")


def verify_source_wiring() -> None:
    group = "C. 源头 _instantiate_agent 接线"
    import domains.agent.application.lifecycle.runtime_composition as rc
    import domains.agent.application.lifecycle.instance_manager as im

    # 同样用 AST 判定实际关键字实参，避免把注释里的历史拼写误判为缺陷。
    tree = ast.parse(inspect.getsource(rc._instantiate_agent).lstrip())
    kwarg_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg:
                    kwarg_names.add(kw.arg)
    record(group, "_instantiate_agent 透传 preset_type",
           "OK" if "preset_type" in kwarg_names else "FAIL")
    record(group, "_instantiate_agent 使用正确拼写 tool_call_mode",
           "OK" if "tool_call_mode" in kwarg_names else "FAIL",
           f"实得关键字={sorted(kwarg_names)}")
    record(group, "_instantiate_agent 不再使用错误拼写 tool_callmode",
           "OK" if "tool_callmode" not in kwarg_names else "FAIL",
           "仍有 tool_callmode 实参" if "tool_callmode" in kwarg_names else "")

    params = inspect.signature(im.AgentInstanceManager.create_agent_instance).parameters
    for name in ("tool_call_mode", "preset_type", "reflection_prompts",
                 "plan_prompts", "reflection_max_iterations", "tool_call_listener"):
        record(group, f"create_agent_instance 接受 {name}",
               "OK" if name in params else "FAIL")


def verify_reply_strategy_selection() -> None:
    """验证 reply 的生命周期外壳 + _reply_impl 的策略主体。"""
    group = "D. reply / _reply_impl 分层"
    import domains.agent.domain.aggregates.react_agent as ra

    reply_src = inspect.getsource(ra.ReActAgent.reply)
    impl_src = inspect.getsource(ra.ReActAgent._reply_impl)

    has_shell = all(h in reply_src for h in ("pre_reply", "post_reply", "model_error"))
    record(group, "reply 仅保留 hook 生命周期外壳", "OK" if has_shell else "FAIL")
    record(group, "reply 内部调用 _reply_impl",
           "OK" if "_reply_impl" in reply_src else "FAIL")
    no_body = "run_react_reply" not in reply_src
    record(group, "reply 不再内联策略主体", "OK" if no_body else "FAIL")
    for strategy in ("_reply_reflection", "_reply_plan_solve", "_reply_text_parse"):
        record(group, f"_reply_impl 路由到 {strategy}",
               "OK" if strategy in impl_src else "FAIL")


async def verify_strategies_run() -> None:
    """真正跑一遍三条非主循环策略，确认不再是死代码。"""
    import domains.agent.domain.aggregates.react_agent as ra
    import domains.agent.domain.value_objects.message as message

    group = "E. 三条策略实际可运行"

    # --- text_parse：模型直接给出终局文本（无工具标记）---
    agent = make_agent(preset_type="simple", model=FakeModel(["最终答案：42"]))
    try:
        out = await agent._reply_text_parse(message.Msg("verify-agent", "1+1?", "user"))
        ok = isinstance(out, message.Msg) and out.content == "最终答案：42"
        record(group, "_reply_text_parse 可执行", "OK" if ok else "FAIL",
               f"返回={out!r}"[:120])
    except Exception as e:  # noqa: BLE001
        record(group, "_reply_text_parse 可执行", "FAIL", f"{type(e).__name__}: {e}")

    # --- plan_solve：先返回可执行计划，再逐步执行 ---
    plan_text = "计划如下：\n'''python\n['第一步', '第二步']\n'''\n"
    agent = make_agent(
        preset_type="plan_solve",
        model=FakeModel([plan_text, "步骤1结果", "步骤2结果"]),
    )
    try:
        out = await agent._reply_plan_solve(message.Msg("verify-agent", "做个计划", "user"))
        ok = isinstance(out, message.Msg) and "步骤1结果" in (out.content or "")
        record(group, "_reply_plan_solve 可执行", "OK" if ok else "FAIL",
               f"调用次数={agent.model.calls} 返回={(out.content or '')[:60]!r}")
    except Exception as e:  # noqa: BLE001
        record(group, "_reply_plan_solve 可执行", "FAIL", f"{type(e).__name__}: {e}")

    # --- reflection：首答 -> 反思判定「无需改进」-> 直接收敛 ---
    agent = make_agent(
        preset_type="reflection",
        model=FakeModel(["初始答案", "无需改进"]),
    )
    try:
        out = await agent._reply_reflection(message.Msg("verify-agent", "写点东西", "user"))
        ok = isinstance(out, message.Msg) and out.content == "初始答案"
        record(group, "_reply_reflection 可执行", "OK" if ok else "FAIL",
               f"调用次数={agent.model.calls} 返回={(out.content or '')[:60]!r}")
    except Exception as e:  # noqa: BLE001
        record(group, "_reply_reflection 可执行", "FAIL", f"{type(e).__name__}: {e}")

    # --- _generate_turn 是三条策略共用的正确模型调用范式 ---
    agent = make_agent(model=FakeModel(["hello"]))
    try:
        turn = await agent._generate_turn(iteration=0)
        ok = getattr(turn, "full_text", None) == "hello"
        record(group, "_generate_turn 统一模型调用范式", "OK" if ok else "FAIL",
               f"full_text={getattr(turn,'full_text',None)!r}")
    except Exception as e:  # noqa: BLE001
        record(group, "_generate_turn 统一模型调用范式", "FAIL",
               f"{type(e).__name__}: {e}")


async def main() -> int:
    print("=" * 72)
    print("Agent 模块架构验证：ReActAgent 单类 + preset_type 配置预设")
    print("=" * 72)
    verify_routing_table()
    verify_constructor_wiring()
    verify_source_wiring()
    verify_reply_strategy_selection()
    await verify_strategies_run()

    ok = sum(1 for r in RESULTS if r[2] == "OK")
    fail = sum(1 for r in RESULTS if r[2] == "FAIL")
    info = sum(1 for r in RESULTS if r[2] == "INFO")
    print("=" * 72)
    print(f"合计：OK={ok}  FAIL={fail}  INFO={info}")
    if fail:
        print("\n失败明细：")
        for g, i, s, d in RESULTS:
            if s == "FAIL":
                print(f"  - {g} :: {i}  {d}")
    print("=" * 72)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
