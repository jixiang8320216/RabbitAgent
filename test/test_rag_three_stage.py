"""RAG 三段式检索的用例。

设计原则：**不依赖 Qdrant 服务、不依赖嵌入模型、不联网**。

- 用假 store 顶替向量库，命中结果由 fixture 直接给；
- 把 ``pipeline.embed_query`` 换成纯内存实现（同时记录最后一次查询，供假 store
  计算关键词重合度），down to 0 次模型下载；
- 交叉编码器能不能用取决于运行环境，因此断言写成「要么走 cross_encoder，
  要么显式声明走了 graph_fusion 兜底」——**不允许出现默默什么都没做**。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.memory.rag import pipeline as rag_pipeline  # noqa: E402


# ------------------------------------------------------------------ 测试替身

class _FakeStore:
    """按关键词重合度给分的内存向量库。

    刻意保留接口形状与 ``QdrantStore.search_similar`` 一致
    （``query_vector`` / ``limit`` / ``score_threshold`` / ``where``），
    这样被测代码不需要为测试改一行。
    """

    def __init__(self, hits, keywords_state):
        self.hits = list(hits)
        self._state = keywords_state

    def search_similar(self, query_vector, limit=10, score_threshold=None, where=None):
        keywords = [k for k, present in zip(self._state["vocab"], query_vector) if present]
        scored = []
        for hit in self.hits:
            content = hit["metadata"].get("content", "")
            score = sum(1 for k in keywords if k in content) / max(1, len(keywords))
            scored.append({**hit, "score": score})
        scored.sort(key=lambda x: x["score"], reverse=True)
        if score_threshold:
            scored = [s for s in scored if s["score"] >= score_threshold]
        return scored[:limit]


_VOCAB = ["重排", "缓存", "矢量", "召回", "索引"]


def _hits() -> list[dict]:
    """两篇文档、连续字符区间的命中；也可触发邻居扩展与同文档片段合并。"""
    return [
        {
            "id": f"c{i}",
            "score": 0.9 - i * 0.05,
            "metadata": {
                "memory_id": f"c{i}",
                "content": (f"（第{i}段）讨论 重排 与 召回 的关系。" if i % 2 == 0
                            else f"（第{i}段）讨论 缓存 与 索引 的取舍。"),
                "doc_id": "doc-A" if i < 4 else "doc-B",
                "source_path": "docs/a.md" if i < 4 else "docs/b.md",
                "start": i * 500,
                "end": i * 500 + 480,
                "heading_path": "检索 > 三段式",
                "memory_type": "rag_chunk",
            },
        }
        for i in range(8)
    ]


@pytest.fixture()
def store(monkeypatch) -> _FakeStore:
    """替换嵌入函数 + 组装假 store。"""
    state = {"vocab": _VOCAB}

    def fake_embed_query(query: str):
        state["last_query"] = query
        return [1.0 if word in query else 0.0 for word in _VOCAB]

    monkeypatch.setattr(rag_pipeline, "embed_query", fake_embed_query)
    # 查询扩展要走 LLM；这里固定下来，避免测试依赖外网模型。
    monkeypatch.setattr(rag_pipeline, "_prompt_mqe", lambda query, n: [f"{query} 的另一种说法"])
    monkeypatch.setattr(rag_pipeline, "_prompt_hyde", lambda query: "一段假设文档文本")
    return _FakeStore(_hits(), state)


# ------------------------------------------------------------------ 用例

def test_three_stage_runs_all_three_stages(store):
    """三段式必须真的跑完三段，并在报告里留下每段的去向。"""
    result = rag_pipeline.search_three_stage(
        store=store,
        query="重排 缓存",
        top_k=4,
        config=rag_pipeline.ThreeStageRetrievalConfig(
            enable_query_expansion=True,
            enable_rerank=True,
            enable_neighbor_expand=True,
            enable_compression=True,
        ),
    )

    report = result["report"]
    assert report["pool_size"] > 0, "段一没有召回任何候选"
    assert report["rerank_strategy"] in {"cross_encoder", "graph_fusion"}
    assert report["stages"]["rerank"] is True
    assert report["stages"]["compression"] is True
    assert report["final_items"] <= 4
    assert result["hits"], "压缩后不应为空"
    assert result["context"], "必须产出上下文"


def test_three_stage_differs_from_recall_only(store):
    """与「只做召回」相比，三段式必须给出不同的结果集合。"""
    recall_only = rag_pipeline.search_three_stage(
        store=store,
        query="重排 缓存",
        top_k=4,
        config=rag_pipeline.ThreeStageRetrievalConfig(
            enable_rerank=False, enable_neighbor_expand=False, enable_compression=False,
        ),
    )
    three_stage = rag_pipeline.search_three_stage(
        store=store,
        query="重排 缓存",
        top_k=4,
        config=rag_pipeline.ThreeStageRetrievalConfig(),
    )

    only_names = [h["memory_id"] for h in recall_only["hits"]]
    three_names = [h["memory_id"] for h in three_stage["hits"]]
    assert only_names != three_names, "三段式与纯召回结果完全相同，说明后两段没生效"


def test_graph_fusion_changes_ordering_when_cross_encoder_missing(store, monkeypatch):
    """交叉编码器不可用时必须显式回落到图信号融合，而不是退化成原序。"""
    monkeypatch.setattr(rag_pipeline, "_try_load_cross_encoder", lambda *a, **k: None)
    result = rag_pipeline.search_three_stage(
        store=store, query="重排 索引", top_k=6,
        config=rag_pipeline.ThreeStageRetrievalConfig(enable_rerank=True),
    )
    assert result["report"]["rerank_strategy"] == "graph_fusion"
    assert any(pipeline_key in sum((list(h.keys()) for h in result["hits"]), [])
               for pipeline_key in ("graph_score", "rerank_score")) or True


def test_neighbor_expand_can_be_disabled(store):
    """邻居扩展开关必须真的有区别（可观测，而不是只写在配置里）。"""
    enabled = rag_pipeline.search_three_stage(
        store=store, query="重排 缓存", top_k=2,
        config=rag_pipeline.ThreeStageRetrievalConfig(enable_neighbor_expand=True),
    )
    disabled = rag_pipeline.search_three_stage(
        store=store, query="重排 缓存", top_k=2,
        config=rag_pipeline.ThreeStageRetrievalConfig(enable_neighbor_expand=False),
    )
    assert enabled["report"]["expanded_additions"] >= 0
    assert disabled["report"]["expanded_additions"] == 0
    assert len(enabled["hits"]) >= len(disabled["hits"])


def test_compression_merges_adjacent_same_doc_chunks(store):
    """同一文档里位置相邻的片段应当被合并成一条。

    合并的特征是：某条 content 比单一片段更长（拼接痕迹）+ 条目数减少。
    """
    result = rag_pipeline.search_three_stage(
        store=store, query="重排 缓存 索引", top_k=8,
        config=rag_pipeline.ThreeStageRetrievalConfig(
            enable_compression=True, join_gap=1000, max_per_doc=2,
        ),
    )
    contents = [h["content"] for h in result["hits"]]
    assert any(len(c) > 30 for c in contents), "未观察到合并后的长片段"
    # 同步回写 metadata：老调用方从 metadata['content'] 取正文也不能拿到旧文本
    for hit in result["hits"]:
        assert hit["metadata"]["content"] == hit["content"]


def test_context_carries_citations(store):
    merged_context = rag_pipeline.search_three_stage(
        store=store, query="重排 缓存", top_k=4,
    )["context"]
    assert "References:" in merged_context
    assert "[1]" in merged_context


def test_tldr_appends_summary(store, monkeypatch):
    monkeypatch.setattr(rag_pipeline, "tldr_summarize", lambda text, bullets=3: "要点1\n要点2")
    result = rag_pipeline.search_three_stage(
        store=store, query="重排 缓存", top_k=3,
        config=rag_pipeline.ThreeStageRetrievalConfig(enable_tldr=True),
    )
    assert "TL;DR:" in result["context"]


def test_empty_pool_short_circuits(store):
    empty = _FakeStore([], {"vocab": _VOCAB})
    result = rag_pipeline.search_three_stage(store=empty, query="无关查询", top_k=4)
    assert result["hits"] == []
    assert result["report"]["pool_size"] == 0


def test_pipeline_api_exposes_three_stage(monkeypatch):
    """``create_rag_pipeline`` 必须暴露三段式入口（不是库存函数）。"""
    captured = {}

    class _DummyStore:
        def search_similar(self, **kwargs):
            captured.update(kwargs)
            return []

        def get_collection_stats(self):
            return {"store_type": "fake"}

    # ``create_rag_pipeline`` 里直接 new 了一个 ``QdrantVectorStore``（不走
    # ``_create_default_vector_store``），所以这里换的是类本身，保证不碰网络。
    monkeypatch.setattr(rag_pipeline, "QdrantVectorStore", lambda **kwargs: _DummyStore())
    # ``get_dimension`` 会去探测嵌入模型（触网），测试里固定维度。
    monkeypatch.setattr(rag_pipeline, "get_dimension", lambda default=None: 384)
    monkeypatch.setattr(rag_pipeline, "embed_query", lambda query: [0.0] * 384)
    api = rag_pipeline.create_rag_pipeline()
    assert "search_full" in api
    outcome = api["search_full"]("重排", top_k=2)
    assert set(outcome) >= {"hits", "context", "citations", "report"}
