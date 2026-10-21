"""domains.memory 聚合包。

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
    "BaseMemory": "domains.memory.base",
    "DashScopeEmbedding": "domains.memory.embedding",
    "Document": "domains.memory.rag.document",
    "DocumentChunk": "domains.memory.rag.document",
    "DocumentProcessor": "domains.memory.rag.document",
    "DocumentStore": "domains.memory.storage.document_store",
    "EmbeddingModel": "domains.memory.embedding",
    "Entity": "domains.memory.types.semantic",
    "Episode": "domains.memory.types.episodic",
    "EpisodicMemory": "domains.memory.types.episodic",
    "FilterReport": "domains.memory.rag.acl_filter",
    "HuggingFaceEmbedding": "domains.memory.rag._types",
    "LocalTransformerEmbedding": "domains.memory.embedding",
    "MarkdownFrontmatter": "domains.memory.infrastructure.markdown.markdown_memory_repository",
    "MarkdownMemoryEntry": "domains.memory.infrastructure.markdown.markdown_memory_repository",
    "MarkdownMemoryIndex": "domains.memory.infrastructure.markdown.markdown_memory_repository",
    "MarkdownRepository": "domains.memory.infrastructure.markdown.markdown_memory_repository",
    "MarkdownRepositoryError": "domains.memory.infrastructure.markdown.markdown_memory_repository",
    "Memory": "domains.memory.types.working",
    "MemoryConfidence": "domains.memory.domain.memory_entry",
    "MemoryConfig": "domains.memory.base",
    "MemoryDeletionReport": "domains.memory.application.memory_runtime_service",
    "MemoryDocumentScope": "domains.memory.domain.memory_document",
    "MemoryDocumentStore": "domains.memory.infrastructure.memory_document_store",
    "MemoryItem": "domains.memory.base",
    "MemoryManager": "domains.memory.manager",
    "MemoryRuntimeService": "domains.memory.application.memory_runtime_service",
    "MemoryRuntimeSettings": "domains.memory.application.memory_runtime_service",
    "MemoryScope": "domains.memory.domain.memory_entry",
    "MemoryStatus": "domains.memory.domain.memory_entry",
    "MemoryType": "domains.memory.domain.memory_entry",
    "NEO4J_AVAILABLE": "domains.memory.storage.neo4j_store",
    "Neo4jGraphStore": "domains.memory.storage.neo4j_store",
    "Perception": "domains.memory.types.perceptual",
    "PerceptualMemory": "domains.memory.types.perceptual",
    "QDRANT_AVAILABLE": "domains.memory.storage.qdrant_store",
    "QdrantConnectionManager": "domains.memory.storage.qdrant_store",
    "QdrantVectorStore": "domains.memory.storage.qdrant_store",
    "Relation": "domains.memory.types.semantic",
    "SQLiteDocumentStore": "domains.memory.storage.document_store",
    "SemanticMemory": "domains.memory.types.semantic",
    "SentenceTransformerEmbedding": "domains.memory.rag._types",
    "TFIDFEmbedding": "domains.memory.embedding",
    "WorkingMemory": "domains.memory.types.working",
    "add_chunk_metadata": "domains.memory.rag.document",
    "build_graph_from_chunks": "domains.memory.rag.pipeline",
    "compress_ranked_items": "domains.memory.rag.pipeline",
    "compute_graph_signals_from_pool": "domains.memory.rag.pipeline",
    "create_document": "domains.memory.rag.document",
    "create_embedding_model": "domains.memory.embedding",
    "create_embedding_model_with_fallback": "domains.memory.embedding",
    "create_rag_pipeline": "domains.memory.rag.pipeline",
    "document_acl": "domains.memory.rag.acl_filter",
    "embed_query": "domains.memory.rag.pipeline",
    "expand_neighbors_from_pool": "domains.memory.rag.pipeline",
    "filter_by_acl": "domains.memory.rag.acl_filter",
    "filter_chunks": "domains.memory.rag.document",
    "get_dimension": "domains.memory.embedding",
    "get_text_embedder": "domains.memory.embedding",
    "index_chunks": "domains.memory.rag.pipeline",
    "is_visible": "domains.memory.rag.acl_filter",
    "load_and_chunk_texts": "domains.memory.rag.pipeline",
    "load_text_file": "domains.memory.rag.document",
    "merge_chunks": "domains.memory.rag.document",
    "merge_snippets": "domains.memory.rag.pipeline",
    "merge_snippets_grouped": "domains.memory.rag.pipeline",
    "normalize_for_hash": "domains.memory.domain.memory_entry",
    "rank": "domains.memory.rag.pipeline",
    "refresh_embedder": "domains.memory.embedding",
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
