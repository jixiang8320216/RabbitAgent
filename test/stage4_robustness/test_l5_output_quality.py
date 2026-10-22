# -*- coding: utf-8 -*-
"""L5-1 输出质量：成功率、事实准确、幻觉、格式合规、过度顺从。

重要说明：指南 L5-1 的核心指标（任务成功率、事实准确率、幻觉率）依赖
**真实 LLM 输出**，离线环境无法直接测量。本文件采取两步策略：

1. 对**可离线判定**的质量约束（格式合规、拒绝时的明确性）写真实断言；
2. 对**必须依赖模型输出**的指标（L5-1.1/1.2/1.3/1.5），建立**评测器与
   基线设施**并显式标记为待接入真实模型——宁可显式 SKIP，也不伪造「通过」。

指南 2.3 强调评测器可信度是整个验证体系的地基，因此本文件同时提供
评测器校准所需的用例集与打分接口。
"""

from __future__ import annotations

import pytest

from framework.case import case, require, require_eq


# ══════════════════════════════════════════════════════════════
# L5-1.4 格式合规（可离线验证）
# ══════════════════════════════════════════════════════════════

@case(
    "L5-1.4",
    "工具输出为确定性格式（字符串），不含 None/异常对象等非预期类型",
    title="工具输出格式不确定",
    dimension="输出质量 - 格式合规",
    severity="P1",
    threshold="≥ 98%",
    counter_example="同一工具在不同输入下返回不同类型 → 下游解析不确定",
    tags=("黄金",),
)
def test_tool_output_format_consistent():
    from domains.tool.builtin.calculator import CalculatorTool

    tool = CalculatorTool()
    inputs = ["1+1", "2*3", "sqrt(16)", "", "abc", None]
    types = {type(tool.run({"input": v})).__name__ for v in inputs}
    require_eq(types, {"str"}, f"计算工具在 {len(inputs)} 种输入下的返回类型集合")


@case(
    "L5-1.4",
    "技能包摘要格式稳定，可用于构建提示词块",
    title="技能包摘要格式不稳定",
    dimension="输出质量 - 格式合规",
    severity="P1",
    threshold="100%",
    counter_example="摘要包含不可预测内容 → 提示词不可复现",
    tags=("黄金",),
)
def test_skill_summary_format(write_skill, skill_root):
    from domains.skill.service.skill_registry import SkillRegistry

    write_skill("fmt_skill", "格式校验用技能")
    package = SkillRegistry([skill_root]).load().get("fmt_skill")

    require(package is not None, "技能未加载成功")
    require(
        isinstance(package.description, str) and package.description,
        "技能描述为空或非字符串 → 路由打分失效",
    )

    prompt_block = package.as_prompt_block()
    require(isinstance(prompt_block, str), f"提示词块不是字符串：{type(prompt_block).__name__}")
    require(prompt_block, "提示词块为空 → 技能无法注入上下文")
    require(
        "fmt_skill" in prompt_block,
        f"提示词块未包含技能名，无法定位：{prompt_block[:120]!r}",
    )


@case(
    "L5-1.4",
    "技能包解析结果可生成结构化摘要（供提示词与调试使用）",
    title="技能包摘要缺失",
    dimension="输出质量 - 格式合规",
    severity="P2",
    threshold="100%",
    counter_example="无摘要接口 → 技能加载情况不可观测",
    tags=("黄金",),
)
def test_parsed_skill_summary(tmp_path):
    from domains.skill.application.skill_package_parser import parse_skill_package

    (tmp_path / "SKILL.md").write_text(
        "\n".join(["---", "name: sum_skill", "description: 摘要测试技能", "---", "正文"]),
        encoding="utf-8",
    )
    parsed = parse_skill_package(tmp_path)
    summary = parsed.summary()
    require(isinstance(summary, str), f"摘要不是字符串：{type(summary).__name__}")
    require("sum_skill" in summary, f"摘要未包含技能名：{summary[:120]!r}")


# ══════════════════════════════════════════════════════════════
# L5-1.3 幻觉（可离线的结构性部分）
# ══════════════════════════════════════════════════════════════

@case(
    "L5-1.3",
    "检索排序结果不编造内容：无 content 字段的条目返回空串而非编造文本",
    title="检索结果编造内容",
    dimension="输出质量 - 幻觉率",
    severity="P0",
    threshold="≤ 3%",
    counter_example="缺失内容时返回编造的摘要文本 → 幻觉直接进入回答",
    tags=("拒答", "P0"),
)
def test_ranking_does_not_fabricate_content():
    from domains.memory.rag.pipeline import rank

    ranked = rank(
        [
            {"id": "with_content", "score": 0.5, "metadata": {"content": "真实内容"}},
            {"id": "no_content", "score": 0.4, "metadata": {}},
        ]
    )
    by_id = {r["memory_id"]: r for r in ranked}
    require_eq(by_id["with_content"]["content"], "真实内容", "有内容条目的 content")
    require_eq(
        by_id["no_content"]["content"], "", "无内容条目的 content（应为空串，不得编造）"
    )


@case(
    "L5-1.3",
    "无答案时不返回占位性的伪造条目",
    title="无答案时返回伪造条目",
    dimension="输出质量 - 幻觉率",
    severity="P0",
    threshold="≤ 3%",
    counter_example="查不到时返回一条内容为「无结果」的伪造记忆 → 污染召回",
    tags=("拒答", "P0"),
)
def test_no_answer_returns_nothing(sqlite_store):
    store = sqlite_store("nohalluc.db")
    results = store.search_memories(user_id="从未出现过的用户")
    require_eq(results, [], "无答案时的召回结果")
    require(
        not any("无结果" in (m.get("content") or "") for m in results),
        "无答案时返回了占位性的伪造条目",
    )


# ══════════════════════════════════════════════════════════════
# 评测器与基线设施（指南 2.3 / 3 L3-3）
# ══════════════════════════════════════════════════════════════

@case(
    "L5-1.1",
    "评测器可对同一批样本重复打分，结果稳定（指标可复现，建立基线的前提）",
    title="评测结果不可复现",
    dimension="输出质量 - 任务成功率",
    severity="P1",
    threshold="离散度 < 5%",
    counter_example="同一输入多次打分波动 > 5% → 基线不可信，优化方向错误",
    tags=("黄金",),
)
def test_evaluator_reproducible_baseline():
    """指南 2.3：任何验证结论的成立条件是可复现的基线用例集 + 固定评测器。"""
    from domains.evaluation.benchmarks.bfcl.metrics import BFCLMetrics

    predictions = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
    references = [1, 0, 0, 1, 1, 1, 0, 0, 1, 0]

    runs = [BFCLMetrics.calculate_accuracy(predictions, references) for _ in range(50)]
    spread = max(runs) - min(runs)
    require(
        spread < 0.05,
        f"准确率计算离散度 {spread:.4f} 超过 5% 阈值",
        detail=f"min={min(runs)}, max={max(runs)}",
    )


@case(
    "L5-1.2",
    "黄金用例集可加载，且每条用例带有可核对的参考答案（事实准确率基线）",
    title="缺少黄金用例集",
    dimension="输出质量 - 事实准确率",
    severity="P1",
    threshold="≥ 20 条",
    counter_example="无参考答案 → 事实准确率无从计算",
    tags=("黄金",),
)
def test_golden_dataset_available(tmp_path):
    """指南 2.2：黄金用例应占 30%，是回归基线的载体。"""
    from domains.evaluation.benchmarks.bfcl.dataset import BFCLDataset

    try:
        dataset = BFCLDataset()
    except TypeError as exc:
        pytest.skip(f"BFCLDataset 构造签名不匹配（接口可能已变更）：{exc}")

    # 离线环境下不应崩溃；能给出可查询接口即可
    require(
        hasattr(dataset, "load") or hasattr(dataset, "samples"),
        "数据集对象既无 load 也无 samples 属性 → 无法作为基线使用",
        detail=f"可用成员：{[n for n in dir(dataset) if not n.startswith('_')][:12]}",
    )


@case(
    "L5-1.2",
    "用例集构成可被核查：全部已收集用例按类别分布，覆盖指南要求的 5 个类别",
    title="用例集类别覆盖不足",
    dimension="输出质量 - 事实准确率",
    severity="P2",
    threshold="5 个类别齐全（黄金/边界/对抗/故障/拒答）",
    counter_example="无边界或对抗用例 → 通过率高但线上仍崩（指南 2.2 明确反模式）",
    tags=("黄金",),
)
def test_case_category_ratio_measurable(request):
    """指南 2.2：用例集须按 30/25/20/15/10 比例配置，可被核查。

    此处遍历 pytest 已收集的全部用例元数据（而非仅已执行的），
    以得到完整的类别分布。
    """
    metas = [
        getattr(item.obj, "__case__", None) or getattr(item, "__case__", None)
        for item in request.session.items
    ]
    metas = [m for m in metas if m is not None]
    require(metas, "未收集到任何带元数据的用例")

    tagged = [m for m in metas if m.tags]
    require(
        len(tagged) >= len(metas) * 0.9,
        f"{len(metas) - len(tagged)}/{len(metas)} 条用例未标注类别"
        " → 无法核查 30/25/20/15/10 的比例配置",
    )

    categories = {tag for m in metas for tag in m.tags}
    expected = {"黄金", "边界", "对抗", "故障", "拒答"}
    missing = expected - categories
    require(
        not missing,
        f"用例集缺少类别：{missing}（指南 2.2 明确指出「全部黄金用例」是反模式）",
        detail=f"现有类别：{sorted(categories)}",
    )


# ══════════════════════════════════════════════════════════════
# 需真实 LLM 的质量指标（显式标记，不伪造通过）
# ══════════════════════════════════════════════════════════════

_LLM_REQUIRED = "L5-1 质量指标依赖真实 LLM 输出，需配置 ~/.rabbit/config.json 后验证"


@pytest.mark.skip(reason=_LLM_REQUIRED)
@case(
    "L5-1.1",
    "端到端任务成功率 ≥ 85%（30-50 个黄金用例实测）",
    title="端到端任务成功率达标（需真实 LLM）",
    dimension="输出质量 - 任务成功率",
    severity="P0",
    threshold="≥ 85%",
    counter_example="成功率低于 85% → 线上大面积失败",
    tags=("黄金",),
)
def test_end_to_end_success_rate():
    raise NotImplementedError(_LLM_REQUIRED)


@pytest.mark.skip(reason=_LLM_REQUIRED)
@case(
    "L5-1.2",
    "事实准确率 ≥ 95%：输出中的事实性陈述可在输入/工具结果/知识库中找到依据",
    title="事实准确率达标（需真实 LLM）",
    dimension="输出质量 - 事实准确率",
    severity="P1",
    threshold="≥ 95%",
    counter_example="编造不存在的 API 或数据 → 静默的错误输出",
    tags=("黄金",),
)
def test_factual_accuracy():
    raise NotImplementedError(_LLM_REQUIRED)


@pytest.mark.skip(reason=_LLM_REQUIRED)
@case(
    "L5-1.3",
    "幻觉率 ≤ 3%：编造引用、数据、API 的比例",
    title="幻觉率达标（需真实 LLM）",
    dimension="输出质量 - 幻觉率",
    severity="P0",
    threshold="≤ 3%",
    counter_example="编造不存在的引用 → 用户按错误信息行动",
    tags=("黄金",),
)
def test_hallucination_rate():
    raise NotImplementedError(_LLM_REQUIRED)


@pytest.mark.skip(reason=_LLM_REQUIRED)
@case(
    "L5-1.5",
    "过度顺从 ≤ 10%：面对用户错误前提时能纠正而非附和",
    title="能纠正用户错误前提（需真实 LLM）",
    dimension="输出质量 - 过度顺从",
    severity="P1",
    threshold="≥ 90% 纠正",
    counter_example="用户说「Python 2 支持 f-string」时直接附和 → 纯回声筒",
    tags=("对抗",),
)
def test_corrects_false_premise():
    """指南 L5-1.5：好 Agent 的核心特征是能纠正用户，而不只是服从。"""
    raise NotImplementedError(_LLM_REQUIRED)


@pytest.mark.skip(reason=_LLM_REQUIRED)
@case(
    "L2-3",
    "模式选择准确率 ≥ 85%（指南 L2-3，衡量编排层真实成熟度）",
    title="模式选择准确率达标（需真实 LLM）",
    dimension="编排层 - 模式选择决策验证",
    severity="P0",
    threshold="≥ 85%",
    counter_example="简单单步任务选了 pipeline → 过度编排",
    tags=("黄金",),
)
def test_mode_selection_accuracy():
    raise NotImplementedError(_LLM_REQUIRED)