# -*- coding: utf-8 -*-
"""L1-2 Skills 与 L1-5 RAG：路由命中、参数校验、召回、引用忠实度、无答案处理。

指南 L1-2.1 路由命中率目标 ≥ 90%，L1-5.3 无答案处理 ≥ 98%（幻觉主要来源）。
"""

from __future__ import annotations

import json

import pytest

from framework.case import case, require, require_eq


# ══════════════════════════════════════════════════════════════
# L1-2 Skills
# ══════════════════════════════════════════════════════════════

@case(
    "L1-2.1",
    "技能注册表能从搜索根加载 SKILL.md 并按名检索",
    title="技能加载失败",
    dimension="Skills - 加载正确性",
    severity="P1",
    threshold="100%",
    counter_example="合法技能包未被加载 → 能力不可见",
    tags=("黄金",),
)
def test_skill_registry_load(write_skill, skill_root):
    from domains.skill.service.skill_registry import SkillRegistry

    write_skill("data_analysis", "数据分析与可视化 CSV 处理")
    write_skill("code_review", "review code for bugs and style")
    (skill_root / "no_manifest").mkdir(parents=True, exist_ok=True)  # 无 SKILL.md → 应跳过

    registry = SkillRegistry([skill_root]).load()

    require_eq(sorted(registry.names()), ["code_review", "data_analysis"], "已加载技能名")
    require_eq(len(registry), 2, "技能总数")
    require("data_analysis" in registry, "包含判断")
    require_eq(
        registry.get("data_analysis").description, "数据分析与可视化 CSV 处理", "技能描述"
    )
    require(registry.get("nope") is None, "不存在的技能应返回 None")


@case(
    "L1-2.1",
    "技能路由命中率 ≥ 90%：10 条典型任务描述各自命中预期技能",
    title="技能路由命中率不足",
    dimension="Skills - 加载正确性",
    severity="P1",
    threshold="≥ 90%",
    counter_example="任务描述被路由到错误技能或无匹配",
    tags=("黄金",),
)
def test_skill_routing_accuracy(write_skill, skill_root):
    from domains.skill.service.skill_registry import SkillRegistry

    write_skill("data_analysis", "数据分析与可视化 CSV 处理")
    write_skill("code_review", "review code for bugs and style")
    write_skill("note_taking", "记录与管理本地笔记 markdown")
    write_skill("math_calc", "数学表达式计算求值")

    registry = SkillRegistry([skill_root]).load()

    cases = [
        ("请分析这份 CSV 数据并画图", "data_analysis"),
        ("analyze the CSV data with visualization", "data_analysis"),
        ("帮我 review 这段代码有没有 bug", "code_review"),
        ("review this python code for style issues", "code_review"),
        ("把这些内容记成笔记", "note_taking"),
        ("创建一条 markdown 笔记记录灵感", "note_taking"),
        ("计算这个数学表达式", "math_calc"),
        ("evaluate this math expression 1+2", "math_calc"),
        ("请分析这份 CSV 数据", "data_analysis"),
        ("review this code", "code_review"),
    ]
    misses = []
    for task, expected in cases:
        hits = registry.route(task, top_k=1)
        actual = hits[0].name if hits else None
        if actual != expected:
            misses.append(f"{task[:24]!r} → {actual}（期望 {expected}）")

    hit_rate = (len(cases) - len(misses)) / len(cases) * 100
    require(
        hit_rate >= 90.0,
        f"技能路由命中率 {hit_rate:.0f}% 低于 90% 阈值",
        detail="; ".join(misses),
    )


@case(
    "L1-2.1",
    "无匹配技能时返回空列表，不硬塞不相关技能",
    title="无匹配时返回不相关技能",
    dimension="Skills - 加载正确性",
    severity="P1",
    threshold="100%",
    counter_example="无关描述也返回技能 → 加载无关指令，污染上下文",
    tags=("边界",),
)
@pytest.mark.parametrize("task", ["完全无关的一句话", "", "   ", "zzzz qqqq"])
def test_no_match_returns_empty(write_skill, skill_root, task):
    from domains.skill.service.skill_registry import SkillRegistry

    write_skill("data_analysis", "数据分析")
    registry = SkillRegistry([skill_root]).load()
    require_eq(registry.route(task, top_k=3), [], f"任务 {task!r} 的路由结果")


@case(
    "L1-2.2",
    "同名技能在多个搜索根重复出现时，后者覆盖前者并记录告警",
    title="同名技能冲突未处理",
    dimension="Skills - 冲突处理",
    severity="P1",
    threshold="100% 有明确处理",
    counter_example="同名技能静默冲突 → 加载了非预期的实现",
    tags=("边界",),
)
def test_duplicate_skill_handled(write_skill, tmp_path):
    from domains.skill.service.skill_registry import SkillRegistry

    root_a, root_b = tmp_path / "a", tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _write_skill_in(root_a, "dup", "来自根A")
    _write_skill_in(root_b, "dup", "来自根B")

    registry = SkillRegistry([root_a, root_b]).load()
    require_eq(registry.names(), ["dup"], "去重后的技能名")
    require(
        registry.load_warnings,
        "同名技能重复未产生任何告警 → 冲突被静默处理",
        detail=f"技能描述：{registry.get('dup').description}",
    )


def _write_skill_in(root, name: str, description: str):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "\n".join(["---", f"name: {name}", f"description: {description}", "---", "正文"]),
        encoding="utf-8",
    )


@case(
    "L1-2.3",
    "技能包参数校验：缺必填参数被检出并给出可读提示",
    title="必填参数校验缺失",
    dimension="Skills - 参数校验",
    severity="P1",
    threshold="100%",
    counter_example="缺参时不报错而是乱填 → 用错误参数执行",
    tags=("黄金",),
)
def test_skill_required_params(tmp_path):
    from domains.skill.application.skill_package_parser import parse_skill_package

    (tmp_path / "SKILL.md").write_text(
        "\n".join([
            "---", "name: my_skill", "description: 演示技能",
            "parameters:",
            "  - name: file", "    type: string", "    required: true",
            "  - name: rows", "    type: integer", "    default: 10",
            "---", "正文",
        ]),
        encoding="utf-8",
    )
    pkg = parse_skill_package(tmp_path)

    require_eq([p.name for p in pkg.required_parameters], ["file"], "必填参数列表")
    require_eq(pkg.missing_parameters({}), ["file"], "缺失参数检测")
    ok, problems = pkg.validate_arguments({})
    require(ok is False, "缺必填参数时校验未失败")
    require("file" in " ".join(problems), "错误提示未指明缺失参数名", detail=str(problems))


@case(
    "L1-2.3",
    "技能包参数类型校验：类型不匹配被检出且提示实际类型",
    title="参数类型校验缺失",
    dimension="Skills - 参数校验",
    severity="P1",
    threshold="100%",
    counter_example="类型错误不报错 → 运行期才崩溃",
    tags=("故障", "脏数据"),
)
@pytest.mark.parametrize(
    "provided,ok_expected",
    [
        ({"file": "x"}, True),
        ({"file": 123}, False),
        ({"file": "x", "rows": "no"}, False),
        ({"file": "x", "rows": 10}, True),
    ],
    ids=["正确", "类型错误", "可选参数类型错", "可选参数正确"],
)
def test_skill_param_types(tmp_path, provided, ok_expected):
    from domains.skill.application.skill_package_parser import parse_skill_package

    (tmp_path / "SKILL.md").write_text(
        "\n".join([
            "---", "name: my_skill", "description: 演示技能",
            "parameters:",
            "  - name: file", "    type: string", "    required: true",
            "  - name: rows", "    type: integer", "    default: 10",
            "---", "正文",
        ]),
        encoding="utf-8",
    )
    pkg = parse_skill_package(tmp_path)
    ok, problems = pkg.validate_arguments(provided)
    require_eq(ok, ok_expected, f"参数 {provided} 的校验结果（问题：{problems}）")


@case(
    "L1-2.3",
    "结构非法的技能包在解析阶段被拒绝（抛 SkillPackageParseError）",
    title="非法技能包未被拒绝",
    dimension="Skills - 参数校验",
    severity="P1",
    threshold="100%",
    counter_example="缺少 SKILL.md 也能解析成功 → 加载到空技能",
    tags=("故障",),
)
def test_invalid_skill_rejected(tmp_path):
    from domains.skill.application.skill_package_parser import (
        SkillPackageParseError,
        parse_skill_package,
    )

    with pytest.raises(SkillPackageParseError):
        parse_skill_package(tmp_path)  # 无 SKILL.md


@case(
    "L1-2.5",
    "技能副作用声明与实际类型匹配：写文件/删除/网络类副作用被标记为需确认",
    title="副作用声明与实际行为不一致",
    dimension="Skills - 副作用隔离",
    severity="P0",
    threshold="100%",
    counter_example="file_delete 未标记 needs_confirm → 删除文件无确认",
    tags=("黄金",),
)
def test_side_effect_requires_confirm(tmp_path):
    from domains.skill.application.skill_package_parser import parse_skill_package

    (tmp_path / "SKILL.md").write_text(
        "\n".join([
            "---", "name: risky_skill", "description: 高危技能",
            "side_effects:",
            "  - type: file_delete", "    description: 删除文件",
            "---", "正文",
        ]),
        encoding="utf-8",
    )
    pkg = parse_skill_package(tmp_path)
    require(
        pkg.needs_confirm is True,
        "含 file_delete 副作用的技能未标记为需确认 → 删除操作无确认保护",
        detail=f"side_effects={[(s.type, s.requires_confirm) for s in pkg.side_effects]}",
    )


# ══════════════════════════════════════════════════════════════
# L1-5 RAG
# ══════════════════════════════════════════════════════════════

@case(
    "L1-5.3",
    "无答案时检索入口安全降级为空结果，而非抛出未捕获异常或编造条目",
    title="无答案处理不安全",
    dimension="RAG - 无答案处理",
    severity="P0",
    threshold="≥ 98%",
    counter_example="空查询抛出未捕获异常或返回编造条目",
    tags=("拒答",),
)
def test_empty_query_returns_nothing():
    """search_vectors 需要 Qdrant 服务，此处验证其「不可用/空输入」路径安全降级。

    指南 L6 要求可控故障环境；此处通过空输入触发早退路径。
    """
    from domains.memory.rag.pipeline import search_vectors

    for query in ("", "   "):
        try:
            result = search_vectors(None, query=query)
        except ImportError:
            pytest.skip("需要 qdrant-client，无法验证检索早退路径")
        except Exception as exc:  # noqa: BLE001
            require(
                False,
                f"空查询 {query!r} 导致未捕获异常：{type(exc).__name__}",
                detail=str(exc)[:160],
            )
        else:
            require_eq(result, [], f"空查询 {query!r} 的检索结果")


@case(
    "L1-5.6",
    "融合排序按分数降序排列，相关文档排在前列",
    title="排序质量不达标",
    dimension="RAG - 排序质量",
    severity="P1",
    threshold="Top-1 命中率 ≥ 80%",
    counter_example="高相关文档排在末位 → 模型先看到低相关内容",
    tags=("黄金",),
)
def test_rag_ranking_order():
    from domains.memory.rag.pipeline import rank

    hits = [
        {"id": "low", "score": 0.2, "metadata": {"content": "低相关"}},
        {"id": "high", "score": 0.9, "metadata": {"content": "高相关"}},
        {"id": "mid", "score": 0.5, "metadata": {"content": "中相关"}},
    ]
    ranked = rank(hits)
    scores = [r["score"] for r in ranked]
    require_eq(scores, sorted(scores, reverse=True), "排序后的分数序列")
    require_eq(ranked[0]["memory_id"], "high", "Top-1 结果")
    require_eq(
        [r["memory_id"] for r in ranked], ["high", "mid", "low"], "完整的排序结果"
    )


@case(
    "L1-5.2",
    "融合排序结果保留原始向量分与图信号分，可追溯引用依据",
    title="排序结果丢失归因信息",
    dimension="RAG - 引用忠实度",
    severity="P0",
    threshold="100%",
    counter_example="结果中无 vector_score → 无法验证引用是否支持结论",
    tags=("黄金",),
)
def test_rag_ranking_keeps_attribution():
    from domains.memory.rag.pipeline import rank

    ranked = rank(
        [{"id": "a", "score": 0.8, "metadata": {"content": "内容A"}}],
        graph_signals={"a": 0.3},
    )
    require(ranked, "排序结果为空")
    item = ranked[0]
    for key in ("vector_score", "graph_score", "score"):
        require(key in item, f"排序结果缺少归因字段 {key!r}", detail=f"实际字段：{list(item)}")


@case(
    "L1-5.4",
    "RAG 检索默认不叠加 ACL 过滤时，必须由调用方显式过滤（越权风险需被记录）",
    title="ACL 过滤未接入检索链路",
    dimension="RAG - 权限过滤",
    severity="P0",
    threshold="100%",
    counter_example="检索结果直接进上下文且无 ACL 环节 → 越权读取",
    tags=("对抗",),
)
def test_rag_requires_explicit_acl():
    """本用例记录一个架构事实：ACL 过滤不在 search_vectors 内部。

    若调用方忘记叠加 filter_by_acl，即构成越权读取。
    因此要求项目提供「检索默认安全」的机制，或至少提供强制入口。
    """
    import inspect

    from domains.memory.rag import pipeline

    src = inspect.getsource(pipeline.search_vectors)
    has_acl = any(k in src.lower() for k in ("acl", "filter_by_acl", "is_visible"))
    require(
        has_acl,
        "search_vectors 内部不含任何 ACL 过滤，权限过滤完全依赖调用方自觉调用 "
        "filter_by_acl；任何遗漏即造成跨用户越权读取（P0）",
        detail="建议：将 ACL 上下文设为检索的必填参数，或提供 with_acl() 包装器",
    )


@case(
    "L1-5.1",
    "文档分块与合并保持内容完整，不丢句、不截断关键信息",
    title="文档分块丢失内容",
    dimension="RAG - 召回率",
    severity="P1",
    threshold="≥ 90%",
    counter_example="分块后内容缺失 → 正确答案所在片段被截断",
    tags=("黄金",),
)
def test_document_chunking_preserves_content():
    """文档分块可用性（RAG 召回率的前提）。

    已知缺陷：document.py:96 的 `_split_text` 缩进错误地定义在类体之外
    （模块级函数且带多余 self 参数），而 process_document 在 57 行调用
    `self._split_text(...)` → 任何文档入库都会 AttributeError。
    本用例锁定该判据，修复后自动转绿。
    """
    from domains.memory.rag.document import Document, DocumentProcessor

    text = "。".join(f"这是第{i}句测试内容" for i in range(1, 51)) + "。"
    chunks = DocumentProcessor(chunk_size=200, chunk_overlap=20).process_document(
        Document(content=text, metadata={})
    )

    require(chunks, "分块结果为空")
    for idx, chunk in enumerate(chunks[:3]):
        require(chunk.content.strip(), f"第 {idx} 个分块内容为空")
    joined = "".join(c.content for c in chunks)
    for marker in ("这是第1句测试内容", "这是第50句测试内容"):
        require(
            marker in joined,
            f"分块后丢失内容片段：{marker!r}",
            detail=f"共 {len(chunks)} 块",
        )


def test_split_text_is_class_method():
    """_split_text 必须是 DocumentProcessor 的实例方法。

    当前定义在模块级且签名带多余 self，导致 process_document 必然抛
    AttributeError —— 整个 RAG 文档入库链路不可用。
    """
    from domains.memory.rag.document import DocumentProcessor

    require(
        hasattr(DocumentProcessor, "_split_text"),
        "DocumentProcessor 缺少 _split_text 方法：document.py:96 的定义缩进错误地"
        "落在类体之外（模块级函数 + 多余 self 参数），"
        "process_document 第 57 行调用 self._split_text(...) 必然抛 AttributeError，"
        "RAG 文档入库链路整体不可用",
        detail=f"类上实际可用成员：{[n for n in dir(DocumentProcessor) if not n.startswith('__')]}",
    )


def test_chunk_documents_share_source_doc_id():
    """分块结果应可追溯回源文档，否则引用无法定位到原始出处。"""
    from domains.memory.rag.document import Document, DocumentProcessor

    chunks = DocumentProcessor(chunk_size=120, chunk_overlap=20).process_document(
        Document(content="内容。" * 200, metadata={"source": "test.md"})
    )
    require(chunks, "分块结果为空")
    doc_ids = {c.doc_id for c in chunks}
    require_eq(len(doc_ids), 1, "分块所属文档 ID 数量")
    require(chunks[0].metadata.get("source") == "test.md", "分块元数据未继承来源")


@case(
    "L1-5.3",
    "脏数据（空串、超长、控制字符、注入字符串）不导致分块流程崩溃",
    title="脏数据导致分块崩溃",
    dimension="RAG - 无答案处理",
    severity="P1",
    threshold="≥ 95%",
    counter_example="任一脏数据类型引发异常 → 整个检索流程中断",
    tags=("故障", "脏数据"),
)
def test_dirty_data_does_not_crash(dirty_documents):
    from domains.memory.rag.document import Document, DocumentProcessor

    processor = DocumentProcessor(chunk_size=200, chunk_overlap=20)
    crashed = []
    for label, content in dirty_documents.items():
        try:
            processor.process_document(Document(content=content, metadata={}))
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{label}: {type(exc).__name__}")
    require(
        not crashed,
        f"{len(crashed)}/{len(dirty_documents)} 类脏数据导致分块崩溃",
        detail="; ".join(crashed),
    )


@case(
    "L1-5.4",
    "ACL 过滤在 default_deny 模式下丢弃无 ACL 声明的文档（默认拒绝）",
    title="默认放行策略导致越权",
    dimension="RAG - 权限过滤",
    severity="P0",
    threshold="100%",
    counter_example="default_deny=True 时无 ACL 文档仍被保留",
    tags=("对抗",),
)
def test_acl_default_deny():
    from domains.memory.rag.acl_filter import ACLContext, filter_by_acl, is_visible

    ctx = ACLContext(user_id="alice")
    require(
        is_visible({"id": "no_acl"}, ctx, default_deny=True) is False,
        "default_deny=True 时无 ACL 文档仍可见",
    )
    kept, report = filter_by_acl([{"id": "no_acl"}], ctx, default_deny=True)
    require_eq(kept, [], "default_deny 下的保留结果")
    require_eq(report.dropped, 1, "default_deny 下的丢弃计数")


@case(
    "L1-5.5",
    "ACL 过滤器产出的报告可定位到具体被丢弃的文档 ID（冲突处理可追溯）",
    title="ACL 过滤报告不可追溯",
    dimension="RAG - 冲突处理",
    severity="P1",
    threshold="100%",
    counter_example="只给总数不给ID → 无法定位越权来源",
    tags=("黄金",),
)
def test_acl_report_traceable():
    from domains.memory.rag.acl_filter import ACLContext, filter_by_acl

    docs = [{"id": "a", "acl_users": ["alice"]}, {"id": "b", "acl_users": ["bob"]}]
    _, report = filter_by_acl(docs, ACLContext(user_id="alice"))
    require_eq(report.dropped_ids, ("b",), "被丢弃的文档 ID")
    require(report.had_violation is True, "未标记存在违规")