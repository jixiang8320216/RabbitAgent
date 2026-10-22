# -*- coding: utf-8 -*-
"""L2 编排层：模式清单、模式选择、槽位拓扑与模式选择准确率。

指南 L2-3 的「模式选择准确率 ≥ 85%」比任何单个模式的性能指标都更能反映
编排层的真实成熟度，因此本文件以模式选择为核心，而非「模式能不能跑」。
"""

from __future__ import annotations

import pytest

from fixtures import require_mode_declarations, require_module

from framework.case import case, require, require_eq


# ── L2-1 模式清单 ───────────────────────────────────────────────
@case(
    "L2-1",
    "内置模式清单为 8 个：delegate / roleplay / solo / guarded / groupchat / pipeline / parallel / debate",
    title="内置模式清单与指南不符",
    dimension="编排层 - 单一模式功能验证",
    severity="P0",
    threshold="8 个模式",
    counter_example="模式缺失或数量不符 → 编排能力不完整",
    tags=("黄金",),
)
def test_builtin_modes_exactly_eight():
    from domains.configuration.domain.model.app_mode import BUILTIN_APP_MODES

    expected = {
        "delegate", "roleplay", "solo", "guarded",
        "groupchat", "pipeline", "parallel", "debate",
    }
    require_eq(set(BUILTIN_APP_MODES), expected, "内置模式清单")
    require_eq(len(BUILTIN_APP_MODES), 8, "内置模式数量")


@case(
    "L2-1",
    "模式名校验：非法模式被识别为无效并拒绝",
    title="非法模式未被拒绝",
    dimension="编排层 - 单一模式功能验证",
    severity="P1",
    threshold="100%",
    counter_example="非法模式名被静默接受 → 拼写错误导致运行在未知模式",
    tags=("边界",),
)
@pytest.mark.parametrize(
    "value,valid",
    [
        ("delegate", True),
        ("  DELEGATE  ", True),
        ("Debate", True),
        ("two_layer", False),
        ("unknown", False),
        ("", False),
    ],
)
def test_mode_validation(value, valid):
    from domains.configuration.domain.model.app_mode import is_valid_app_mode

    require_eq(is_valid_app_mode(value), valid, f"模式 {value!r} 的合法性")


@case(
    "L2-1",
    "模式名归一化：去空格并转小写",
    title="模式名归一化失败",
    dimension="编排层 - 单一模式功能验证",
    severity="P1",
    threshold="100%",
    counter_example="' DELEGATE ' 未归一为 'delegate' → 匹配不到声明",
    tags=("边界",),
)
def test_mode_canonicalization():
    from domains.configuration.domain.model.app_mode import canonicalize_app_mode

    require_eq(canonicalize_app_mode("  DELEGATE "), "delegate", "归一化结果")
    require_eq(canonicalize_app_mode("GroupChat"), "groupchat", "归一化结果")


@case(
    "L2-1",
    "每个内置模式都有完整的槽位声明，且 root_agent 在槽位集合内",
    title="模式声明缺少 root 槽位",
    dimension="编排层 - 单一模式功能验证",
    severity="P0",
    threshold="8/8 模式",
    counter_example="某模式无声明或 root 不在槽位内 → 装配时找不到根 Agent",
    tags=("黄金",),
)
def test_all_modes_have_root_slot():
    from domains.configuration.domain.model.app_mode import BUILTIN_APP_MODES

    definitions = require_mode_declarations()
    require_eq(set(definitions), set(BUILTIN_APP_MODES), "模式声明的键集合")

    broken = []
    for name, definition in definitions.items():
        decl = definition.declaration
        if decl.name != name:
            broken.append(f"{name}: 声明名为 {decl.name!r}")
        if decl.root_agent not in decl.agent_slots:
            broken.append(f"{name}: root {decl.root_agent!r} 不在槽位 {list(decl.agent_slots)}")
        if not decl.agent_slots:
            broken.append(f"{name}: 槽位为空")
    require(not broken, f"{len(broken)} 个模式声明不完整", detail="; ".join(broken))


@case(
    "L2-1",
    "多槽位模式的拓扑符合设计：guarded 双层、debate 三方对抗",
    title="多槽位模式拓扑错误",
    dimension="编排层 - 单一模式功能验证",
    severity="P1",
    threshold="100%",
    counter_example="guarded 只有 1 个槽位 → 规划-执行分离未实现",
    tags=("黄金",),
)
def test_multi_slot_topology():
    defs = require_mode_declarations()
    slots = {n: set(d.declaration.agent_slots) for n, d in defs.items()}

    require_eq(len(slots["guarded"]), 2, "guarded 模式的槽位数（规划 + 执行）")
    require_eq(len(slots["debate"]), 3, "debate 模式的槽位数（裁判 + 正方 + 反方）")
    require_eq(len(slots["parallel"]), 2, "parallel 模式的槽位数")
    require_eq(len(slots["pipeline"]), 2, "pipeline 模式的槽位数")


@case(
    "L2-1",
    "单槽位模式（solo / roleplay / delegate）不被过度编排",
    title="简单模式被过度编排",
    dimension="编排层 - 单一模式功能验证",
    severity="P1",
    threshold="100%",
    counter_example="solo 声明了多个槽位 → 单Agent 场景白白付出编排开销",
    tags=("黄金",),
)
@pytest.mark.parametrize("mode", ["solo", "roleplay", "delegate"])
def test_simple_modes_not_over_orchestrated(mode):
    defs = require_mode_declarations()
    require_eq(len(defs[mode].declaration.agent_slots), 1, f"{mode} 模式的槽位数")


# ── L2-3 模式选择 ───────────────────────────────────────────────
@case(
    "L2-3",
    "应用模式按「配置文件 > 环境变量 > 默认值」的优先级解析",
    title="模式解析优先级失效",
    dimension="编排层 - 模式选择决策验证",
    severity="P1",
    threshold="100%",
    counter_example="环境变量覆盖了显式配置 → 用户设置被忽略",
    tags=("黄金",),
)
def test_mode_resolution_priority(monkeypatch):
    from domains.configuration.infrastructure.adapter.app_mode_resolver import load_app_mode

    monkeypatch.setenv("RABBIT_APP_MODE", "solo")
    require_eq(
        load_app_mode({"app_mode": "debate"}), "debate", "配置文件优先级下的模式"
    )
    require_eq(load_app_mode({}), "solo", "仅环境变量时的模式")
    monkeypatch.delenv("RABBIT_APP_MODE", raising=False)
    require_eq(load_app_mode({}), "delegate", "无配置时的默认模式")


@case(
    "L2-3",
    "非法模式配置安全回退到默认模式，不导致启动失败",
    title="非法模式配置导致启动失败",
    dimension="编排层 - 模式选择决策验证",
    severity="P1",
    threshold="100% 不抛异常",
    counter_example="配置了非法模式导致应用无法启动",
    tags=("故障",),
)
def test_invalid_mode_config_falls_back(monkeypatch):
    from domains.configuration.infrastructure.adapter.app_mode_resolver import load_app_mode

    monkeypatch.delenv("RABBIT_APP_MODE", raising=False)
    try:
        mode = load_app_mode({"app_mode": "完全不存在的模式"})
    except Exception as exc:  # noqa: BLE001
        require(
            False,
            f"非法模式配置导致异常：{type(exc).__name__}: {exc}",
            detail="配置错误应回退默认，而非阻断启动",
        )
    else:
        require(
            mode in {"delegate", "solo", "roleplay", "guarded",
                     "groupchat", "pipeline", "parallel", "debate"},
            f"非法配置回退到了非内置模式：{mode!r}",
        )


@case(
    "L2-3",
    "模式注册表支持注册、查询与列举，且返回副本防外部篡改",
    title="模式注册表能力缺失",
    dimension="编排层 - 模式选择决策验证",
    severity="P1",
    threshold="100%",
    counter_example="注册表无法查询 → 编排器找不到模式声明",
    tags=("黄金",),
)
def test_model_registry_roundtrip():
    registry_mod = require_module("domains.agent.application.registry.model_registry")
    registry = registry_mod.ModelRegistry()
    for name, definition in require_mode_declarations().items():
        registry.register(definition, source="builtin")

    require_eq(len(registry), 8, "注册表中的模式数量")
    guarded = registry.get("guarded")
    require(guarded is not None, "查询 guarded 模式返回 None")
    require_eq(guarded.name, "guarded", "guarded 模式的声明名")
    require(registry.get("nope") is None, "查询不存在的模式应返回 None")
    require_eq(len(registry.available_modes()), 8, "可用模式数量")


# ── 模式差异的真实实现 ──────────────────────────────────────────
@case(
    "L2-2",
    "各模式在运行时存在可区分的执行分支，而非全部退化为默认行为",
    title="pipeline/parallel/debate 无差异化实现",
    dimension="编排层 - 跨模式横向验证",
    severity="P0",
    threshold="≥ 2 个模式有运行时分支",
    counter_example="多模式仅有声明无实现 → 模式选择无实际效果",
    tags=("黄金",),
)
def test_modes_have_runtime_branches():
    """指南 L2-2：真正要验证的是「模式选择是否正确」，前提是模式本身有差异。"""
    import pathlib

    project = pathlib.Path(__file__).resolve().parents[2]
    runtime_dir = project / "domains" / "runtime" / "application" / "orchestrators"
    sources = {
        p.name: p.read_text(encoding="utf-8-sig", errors="ignore")
        for p in runtime_dir.glob("*.py")
    }
    blob = "\n".join(sources.values())

    imported = [
        mode
        for mode in ("pipeline", "parallel", "debate", "groupchat", "guarded", "roleplay")
        if f'"{mode}"' in blob or f"'{mode}'" in blob
    ]
    require(
        len(imported) >= 2,
        "orchestrators 目录中仅 "
        f"{imported} 有运行时分支，pipeline/parallel/debate 仅有槽位声明而无执行逻辑 "
        "→ 模式下沉为装饰性配置，选择错误模式也无法察觉",
        detail=f"已扫描 {len(sources)} 个 orchestrator 文件",
    )


@case(
    "L2-2",
    "AgentRole 解析优先级为「显式 > 槽位声明 > 槽位形态推断 > GENERAL 兜底」",
    title="AgentRole 解析优先级错误",
    dimension="编排层 - 跨模式横向验证",
    severity="P1",
    threshold="100%",
    counter_example="槽位角色覆盖显式角色 → 编排意图被忽略",
    tags=("黄金",),
)
def test_agent_role_resolution():
    from types import SimpleNamespace

    from domains.agent.domain.entities.agent_role import AgentRole

    # 显式角色
    require_eq(str(AgentRole.resolve("orchestrator")), "orchestrator", "显式角色解析")
    require_eq(str(AgentRole.resolve(AgentRole.SUB_AGENT)), "sub_agent", "枚举形式解析")

    # 槽位声明
    slot = SimpleNamespace(agent_role="sub_agent", agent_type="worker")
    require_eq(str(AgentRole.resolve(None, slot)), "sub_agent", "槽位角色解析")

    # 槽位形态推断
    require_eq(
        str(AgentRole.resolve(None, SimpleNamespace(agent_role=None, agent_type="orchestrator"))),
        "orchestrator",
        "槽位 agent_type 推断",
    )

    # 显式优先于槽位
    slot_orch = SimpleNamespace(agent_role="sub_agent", agent_type="orchestrator")
    require_eq(
        str(AgentRole.resolve("general", slot_orch)), "general", "显式角色优先于槽位"
    )

    # 脏值安全兜底（文档明确：不抛异常）
    for dirty in (None, "不存在的角色", 123, object()):
        require_eq(str(AgentRole.resolve(dirty)), "general", f"脏值 {dirty!r} 的兜底结果")


@case(
    "L2-2",
    "多Agent 调度树可注册根节点并查询子节点关系",
    title="Agent 树关系查询失败",
    dimension="编排层 - 跨模式横向验证",
    severity="P1",
    threshold="100%",
    counter_example="无法查询子节点 → 编排拓扑不可观测",
    tags=("黄金",),
)
def test_agent_tree_relationships():
    tree_mod = require_module("domains.agent.application.scheduling.agent_tree")

    class _StubAgent:
        """最小 agent 桩：AgentTree.register_root 只需 agent.name。"""

        def __init__(self, name: str) -> None:
            self.name = name

    tree = tree_mod.AgentTree()
    root_id = tree.register_root(_StubAgent("root-agent"))
    require(root_id, "注册根节点未返回 ID")
    require(tree.get(root_id) is not None, "按 ID 查询根节点返回 None")
    require_eq(tree.children_of(root_id), [], "根节点的子节点列表")
    require_eq(tree.root_id, root_id, "根节点 ID")


@case(
    "L2-1",
    "反思记忆能记录并按类型检索执行轨迹",
    title="反思轨迹记忆不可用",
    dimension="编排层 - 单一模式功能验证",
    severity="P2",
    threshold="100%",
    counter_example="轨迹记录不可用 → 反思模式无法迭代优化",
    tags=("黄金",),
)
def test_reflection_memory():
    # ``Memory``（反思轨迹记忆）原定义在已删除的
    # ``domains.agent.domain.aggregates.reflection_agent``，现迁至 memory 域。
    memory_cls = require_module(
        "domains.memory.types.reflection_memory"
    ).Memory

    memory = memory_cls()
    memory.add_record("execution", "第一次执行：答案 A")
    memory.add_record("reflection", "反思：答案 A 不完整")

    trajectory = memory.get_trajectory()
    require("第一次执行" in trajectory, "轨迹未包含执行记录", detail=trajectory[:120])
    require("第一次执行" in memory.get_last_execution(), "按类型检索失败")


# ── 需真实 LLM 的端到端模式验证 ─────────────────────────────────
@pytest.mark.skip(reason="L2-1 端到端模式执行需真实 LLM 多次调用，"
                "离线环境无法验证各模式的实际执行行为")
@case(
    "L2-1",
    "8 种模式各自能正常启动并完成端到端任务",
    title="端到端模式执行（需真实 LLM）",
    dimension="编排层 - 单一模式功能验证",
    severity="P0",
    threshold="8/8 模式",
    counter_example="任一模式无法启动或中途失败",
    tags=("黄金",),
)
def test_all_modes_end_to_end():
    raise NotImplementedError("需配置 ~/.rabbit/config.json 后实现")


@pytest.mark.skip(reason="L2-3 模式选择准确率需真实 LLM 判定，"
                "离线环境无法度量模型是否选对模式")
@case(
    "L2-3",
    "模式选择准确率 ≥ 85%：30-50 个任务各自选中期望模式",
    title="模式选择准确率达标（需真实 LLM）",
    dimension="编排层 - 模式选择决策验证",
    severity="P0",
    threshold="≥ 85%",
    counter_example="简单单步任务选了 pipeline（过度编排）",
    tags=("黄金",),
)
def test_mode_selection_accuracy():
    raise NotImplementedError(
        "需真实 LLM + 人工标注的模式期望集；指南 L2-3 建议 30-50 个任务"
    )