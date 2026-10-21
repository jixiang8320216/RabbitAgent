"""domains.memory.rag 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "ACLContext": "domains.memory.rag.acl_filter",
    "Document": "domains.memory.rag.document",
    "DocumentChunk": "domains.memory.rag.document",
    "DocumentProcessor": "domains.memory.rag.document",
    "EmbeddingModel": "domains.memory.rag._types",
    "FilterReport": "domains.memory.rag.acl_filter",
    "HuggingFaceEmbedding": "domains.memory.rag._types",
    "LocalTransformerEmbedding": "domains.memory.rag._types",
    "QdrantVectorStore": "domains.memory.rag.pipeline",
    "SentenceTransformerEmbedding": "domains.memory.rag._types",
    "TFIDFEmbedding": "domains.memory.rag._types",
    "add_chunk_metadata": "domains.memory.rag.document",
    "build_graph_from_chunks": "domains.memory.rag.pipeline",
    "compress_ranked_items": "domains.memory.rag.pipeline",
    "compute_graph_signals_from_pool": "domains.memory.rag.pipeline",
    "create_document": "domains.memory.rag.document",
    "create_rag_pipeline": "domains.memory.rag.pipeline",
    "document_acl": "domains.memory.rag.acl_filter",
    "embed_query": "domains.memory.rag.pipeline",
    "expand_neighbors_from_pool": "domains.memory.rag.pipeline",
    "filter_by_acl": "domains.memory.rag.acl_filter",
    "filter_chunks": "domains.memory.rag.document",
    "index_chunks": "domains.memory.rag.pipeline",
    "is_visible": "domains.memory.rag.acl_filter",
    "load_and_chunk_texts": "domains.memory.rag.pipeline",
    "load_text_file": "domains.memory.rag.document",
    "merge_chunks": "domains.memory.rag.document",
    "merge_snippets": "domains.memory.rag.pipeline",
    "merge_snippets_grouped": "domains.memory.rag.pipeline",
    "rank": "domains.memory.rag.pipeline",
    "rerank_with_cross_encoder": "domains.memory.rag.pipeline",
    "search_vectors": "domains.memory.rag.pipeline",
    "search_vectors_expanded": "domains.memory.rag.pipeline",
    "tldr_summarize": "domains.memory.rag.pipeline",}


def __getattr__(name):
    import importlib

    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
