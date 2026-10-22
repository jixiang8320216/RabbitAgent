"""在**真实拉起的进程**里给新能力打点，回答「代码存在」之外的那个问题：
它到底有没有被调用到。

和单元测试的区别
----------------
``tests/`` 里的用例用假 store / 假配置验证**逻辑正确**；本脚本不改任何行为，
只在真实进程里给目标函数套一层计数器，然后跑：

- **阶段 A**：复用 ``e2e_tui_test`` 的真实 TUI 问答链路，看哪些能力被自然触发；
- **阶段 B**：对 A 阶段不会被自然触发的两项（RAG 三段式、计费上报）**主动创造真实
  条件**再跑一次——真实内存向量库 + 真实交叉编码器重排，而不是用假对象糊过去。

被替换的依赖会逐条写明原因，不做静默降级。

用法::

    python verify_live_capabilities.py
"""

from __future__ import annotations

import asyncio
import math
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COUNTERS: dict[str, int] = {}


def bump(key: str) -> None:
    COUNTERS[key] = COUNTERS.get(key, 0) + 1


def wrap_function(module, attr: str, key: str, target=None) -> bool:
    """给 ``module.attr`` 套计数器。``target`` 非空时说明改的是 target 的属性。"""
    holder = target or module
    original = getattr(holder, attr, None)
    if original is None:
        return False

    def wrapper(*args, **kwargs):
        bump(key)
        return original(*args, **kwargs)

    setattr(holder, attr, wrapper)
    return True


def install_probes() -> list[tuple[str, bool, str]]:
    """挂载全部探针，返回 ``(能力, 是否挂载成功, 说明)``。"""
    installed: list[tuple[str, bool, str]] = []

    import domains.runtime.application.orchestrators.mode_orchestration as mode_orch

    installed.append((
        "8 种模式 · 执行计划解析",
        wrap_function(mode_orch, "resolve_execution_plan", "plan_resolved"),
        "mode_orchestration.resolve_execution_plan",
    ))
    installed.append((
        "8 种模式 · 槽位可见性精化",
        wrap_function(mode_orch, "refine_allowed_agents", "allowed_refined"),
        "mode_orchestration.refine_allowed_agents",
    ))

    import domains.context.application.history_compression as hist

    installed.append((
        "历史消息自动压缩",
        wrap_function(hist, "maybe_compress", "compress_called", target=hist.HistoryCompressionService),
        "HistoryCompressionService.maybe_compress",
    ))

    import domains.billing.application.token_usage_bridge as bridge

    sink_cls = getattr(bridge, "BudgetUsageSink", None)
    installed.append((
        "计费上报（TokenCounter -> BudgetTracker）",
        wrap_function(bridge, "record_usage", "billing_recorded", target=sink_cls) if sink_cls else False,
        "BudgetUsageSink.record_usage（默认关闭，见阶段 B）",
    ))

    import domains.memory.rag.pipeline as rag

    installed.append((
        "RAG 三段式检索",
        wrap_function(rag, "search_three_stage", "rag_three_stage"),
        "pipeline.search_three_stage（需知识库查询，见阶段 B）",
    ))

    return installed


# ---------------------------------------------------------------------------
# 阶段 B：为不会自然触发的能力创造真实条件
# ---------------------------------------------------------------------------

class _LexicalEmbedder:
    """确定性的本地嵌入器（字符直方图 + L2 归一化）。

    为什么替换：项目默认走 dashscope 的 ``text-embedding-v3``，无 key 时回退
    sentence-transformers 并下载 ~90MB 模型，不适合放进一次现场验证。这里换成
    零依赖的字符直方图向量——余弦相似度近似字符重合度，语义虽粗但**是真实向量**，
    会真实写入向量库、真实参与 HNSW 检索。

    刻意保留段二用**真实的交叉编码器**（``ms-marco-MiniLM-L-6-v2`` 已在本地缓存）：
    召回用粗向量、重排用真模型，恰好能验证「段二确实在改顺序」这件事。
    """

    dimension = 384

    def encode(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        vectors = []
        for text in texts:
            acc = [0.0] * self.dimension
            for ch in str(text):
                acc[ord(ch) % self.dimension] += 1.0
            norm = math.sqrt(sum(x * x for x in acc)) or 1.0
            vectors.append([x / norm for x in acc])
        return vectors


_CORPUS = {
    "rag.md": """# RAG 检索

RabbitAgent 的 RAG 采用三段式：召回、重排、压缩。
段一做查询扩展与向量召回，刻意把召回上限放大到 top_k 的四倍，
这样段二才有足够的候选池可以重新排序。
段二用交叉编码器重排，模型不可用时显式回落图信号融合。
段三做同文档相邻片段合并、预算裁剪与带引用的上下文合并。
""",
    "billing.md": """# 计费与预算

计费域维护预算追踪器，记录输入与输出 token 并折算成本。
TokenCounter 在每次真实用量后把数据推给已登记的下游 sink。
未安装 litellm 时只累计条数不累计金额，属于静默降级。
""",
    "modes.md": """# 八种模式

模式编排为每种模式定义独立的执行语义与阶段拓扑。
流水线模式按阶段顺序传递产物，辩论模式正反方交替多轮后由裁判裁决。
并行模式用 asyncio.gather 真正并发执行多个 worker。
""",
}


def run_rag_phase() -> dict:
    """用真实内存向量库 + 真实交叉编码器跑一次三段式检索。"""
    import domains.memory.rag.pipeline as rag
    import domains.memory.storage.qdrant_store as qdrant_store

    from qdrant_client import QdrantClient as _RealClient

    # 真实内存 Qdrant：仍是真实客户端、真实 HNSW 索引与真实检索，只是不落盘。
    qdrant_store.QdrantClient = lambda **kwargs: _RealClient(":memory:")
    _embedder = _LexicalEmbedder()
    rag.get_text_embedder = lambda: _embedder
    rag.get_dimension = lambda default=384: 384

    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, body in _CORPUS.items():
            p = pathlib.Path(tmp) / name
            p.write_text(body, encoding="utf-8")
            paths.append(str(p))

        store = qdrant_store.QdrantVectorStore(
            collection_name="probe_rag", vector_size=384
        )
        chunks = rag.load_and_chunk_texts(paths=paths, chunk_size=120, chunk_overlap=20)
        rag.index_chunks(store=store, chunks=chunks, rag_namespace="probe")

        plain = rag.search_three_stage(
            store=store,
            query="RAG 三段式的每一段分别做什么？",
            top_k=3,
            rag_namespace="probe",
            enable_query_expansion=False,   # MQE/HyDE 需要 LLM，本探针未配置
        )
        expanded = rag.search_three_stage(
            store=store,
            query="RAG 三段式的每一段分别做什么？",
            top_k=3,
            rag_namespace="probe",
            enable_query_expansion=True,    # 段一走完整形态（扩展失败会自行回落）
        )

    def summarize(res: dict) -> dict:
        hits = res.get("hits", []) or []
        report = res.get("report", {}) or {}
        context = res.get("context", "") or ""
        return {
            "hits": len(hits),
            "top_sources": [
                str((h.get("metadata", {}) or {}).get("source_path", "?")).split("\\")[-1]
                for h in hits
            ],
            "pool_size": report.get("pool_size", "?"),
            "rerank_strategy": report.get("rerank_strategy", "?"),
            "stages": report.get("stages", {}),
            "context_chars": len(context),
            "citations": len(res.get("citations", []) or []),
        }

    return {
        "chunks_indexed": len(chunks),
        "plain": summarize(plain),
        "expanded": summarize(expanded),
        "three_stage_calls": COUNTERS.get("rag_three_stage", 0),
    }


def run_billing_phase() -> dict:
    """开启计费开关，用真实 TokenCounter 推一次用量。"""
    import domains.agent.application.context.model_context_factory as factory

    f = factory.ModelContextFactory(attach_billing=True)
    counter = f.get_token_counter()
    counter.set_api_tokens(
        agent_name="probe", prompt_tokens=1000, completed_tokens=500, cached_tokens=100
    )
    tracker = f.budget_tracker
    state = getattr(tracker, "state", None)
    return {
        "attached": tracker is not None,
        "spent_usd": getattr(state, "spent_usd", None) if state else None,
        "records": getattr(state, "record_count", None) if state else getattr(tracker, "record_count", None),
        "default_off": factory.ModelContextFactory().budget_tracker is None,
    }


async def main() -> int:
    print("=" * 72)
    print("真实进程内能力打点")
    print("=" * 72)
    print()

    print("[探针挂载]")
    for label, ok, detail in install_probes():
        print(f"  {'✓' if ok else '✗'} {label} — {detail}")

    print()
    print("[阶段 A] 跑真实 TUI 问答（复用 e2e_tui_test 的同一条链路）")
    import e2e_tui_test

    exit_code = await e2e_tui_test.run(
        include_llm=True,
        question="请用一句话回答：1+1 等于几？",
        timeout=180.0,
    )

    print()
    print("=" * 72)
    print("阶段 A 打点结果（自然触发次数）")
    print("=" * 72)
    rows = [
        ("模式执行计划解析", "plan_resolved", "装配模式时调用"),
        ("槽位可见性精化", "allowed_refined", "每个槽位建 agent 时调用"),
        ("历史压缩检查", "compress_called", "每轮进 LLM 前调用（未超阈值则不压）"),
        ("计费用量上报", "billing_recorded", "默认关闭"),
        ("RAG 三段式检索", "rag_three_stage", "需要有知识库查询"),
    ]
    for label, key, note in rows:
        count = COUNTERS.get(key, 0)
        print(f"  {'已触发' if count else '未触发':6s} {label:12s} 调用 {count:>3d} 次   （{note}）")

    print()
    print("=" * 72)
    print("[阶段 B] 为阶段 A 未触发的两项创造真实条件")
    print("=" * 72)

    rag_info = {}
    try:
        rag_info = run_rag_phase()
        print()
        print("  RAG 三段式（真实内存 Qdrant + 真实交叉编码器重排）：")
        print(f"    索引片段数              : {rag_info['chunks_indexed']}")
        for label, key in (("关查询扩展", "plain"), ("开查询扩展", "expanded")):
            info = rag_info[key]
            print(f"    ── {label} ──")
            print(f"      段一召回池 / 最终命中: {info['pool_size']} -> {info['hits']}")
            print(f"      命中来源              : {info['top_sources']}")
            print(f"      段二重排策略          : {info['rerank_strategy']}")
            print(f"      各段开关              : {info['stages']}")
            print(f"      上下文长度 / 引用数   : {info['context_chars']} 字符 / {info['citations']}")
        print(f"    三段式被调用            : {rag_info['three_stage_calls']} 次")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ RAG 阶段异常: {type(exc).__name__}: {exc}")

    bill_info = {}
    try:
        bill_info = run_billing_phase()
        print()
        print("  计费链路（显式开启 billing.enabled 等价开关）：")
        print(f"    已挂载预算追踪器: {bill_info['attached']}")
        print(f"    累计花费        : {bill_info['spent_usd']}")
        print(f"    上报次数        : {COUNTERS.get('billing_recorded', 0)}")
        print(f"    默认保持关闭    : {bill_info['default_off']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 计费阶段异常: {type(exc).__name__}: {exc}")

    print()
    print("=" * 72)
    print(f"e2e 退出码: {exit_code}（0 = 11/11 通过）")
    print("=" * 72)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
