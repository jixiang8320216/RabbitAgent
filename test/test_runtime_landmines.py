"""锁住两个「静态看代码完全正常、只有跑到特定条件才炸」的运行时地雷。

这两条都不是本次新增功能，而是**验证过程中从真实崩溃里挖出来的既有缺陷**：

1. 惰性导出名与同包子模块同名 -> 子模块导入后遮蔽 ``__getattr__``，进程启动即崩；
2. ``qdrant-client`` 1.19 移除 ``client.search()`` -> 异常被吞，RAG 检索静默返回 0 条。

测试刻意**按触发顺序**构造场景（先导入子模块、再经包属性调用），因为问题只在
特定顺序下出现——随便 import 一下是复现不出来的。
"""

from __future__ import annotations

import importlib
import math
import pathlib
import tempfile

import pytest

import domains.memory.rag.pipeline as rag_pipeline
import domains.memory.storage.qdrant_store as qdrant_store

#: 下面两条用例刻意使用**真实** ``qdrant_client``（内存模式），不做 mock——
#: 地雷 B 的本质是客户端 API 变更，用假客户端验证等于什么都没验证。
#: 但依赖没装时应当 skip 而不是 fail：那是环境问题，不是代码回归。
_SKIP_REASON = "未安装 qdrant-client；向量检索用例需要真实客户端才能验证"


# ---------------------------------------------------------------------------
# 地雷 1：惰性导出名与子模块同名
# ---------------------------------------------------------------------------

def test_file_lock_export_survives_submodule_import():
    """先以子模块形式导入，再经包属性取——必须拿到函数而不是模块。

    这正是启动期崩溃的触发顺序：``user_config_store`` 先
    ``from ...file.file_lock import file_lock``，迁移代码随后
    ``file_persistence.file_lock(...)``。
    """
    importlib.import_module("domains.persistence.infrastructure.file.file_lock_impl")
    package = importlib.import_module("domains.persistence.infrastructure.file")
    assert callable(package.file_lock), (
        "包属性 file_lock 不是可调用对象——惰性导出被子模块绑定遮蔽了"
    )


def test_project_layout_export_survives_submodule_import():
    """同上，针对 project_layout；且两个导出（类与工厂函数）都要正确。"""
    importlib.import_module("domains.configuration.domain.protocol.project_layout_impl")
    package = importlib.import_module("domains.configuration.domain.protocol")
    assert callable(package.project_layout)
    assert hasattr(package.ProjectLayout, "model_fields")


def test_import_as_module_form_still_yields_module():
    """``import a.b.c as x`` 走的是父包同名属性，必须拿到**模块**。

    这一条和上两条是对同一命名空间的两个相反要求，正是当初只能靠「消除同名」
    而不是「急切绑定」来修的原因：急切绑定会让这里拿到函数。
    """
    module = importlib.import_module(
        "domains.persistence.infrastructure.file.file_lock_impl"
    )
    assert hasattr(module, "file_lock")
    assert hasattr(module, "FileLockTimeoutError")


def test_no_lazy_export_shadows_a_submodule():
    """全项目不允许再出现「导出名 == 本包子模块名」。

    直接复用架构检查器的规则 4，避免两份判定逻辑漂移。
    """
    from tools.check_architecture import _collect_lazy_map, _submodule_names
    import ast
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parent.parent
    offenders = []
    for init in root.rglob("*/__init__.py"):
        if "__pycache__" in init.parts:
            continue
        try:
            tree = ast.parse(init.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        subs = _submodule_names(init.parent)
        if not subs:
            continue
        for name, _target, _line in _collect_lazy_map(tree):
            if name in subs:
                offenders.append(f"{init.parent.as_posix()}::{name}")
    assert not offenders, f"存在同名遮蔽的导出: {offenders}"


# ---------------------------------------------------------------------------
# 地雷 2：Qdrant 客户端 API 变更
# ---------------------------------------------------------------------------

class _LexicalEmbedder:
    """字符直方图向量：零依赖、确定性，余弦相似度近似字符重合度。"""

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


def _in_memory_store(monkeypatch, collection: str = "landmine_probe"):
    """造一个真实内存 Qdrant 的 store（真实客户端，只是不落盘）。"""
    from qdrant_client import QdrantClient as _RealClient

    monkeypatch.setattr(
        qdrant_store, "QdrantClient", lambda **kwargs: _RealClient(":memory:")
    )
    monkeypatch.setattr(rag_pipeline, "get_text_embedder", lambda: _LexicalEmbedder())
    monkeypatch.setattr(rag_pipeline, "get_dimension", lambda default=384: 384)
    return qdrant_store.QdrantVectorStore(
        collection_name=collection, vector_size=384
    )


def test_vector_search_returns_hits_with_installed_client(monkeypatch):
    """索引进去的东西必须能被检索出来。

    在 qdrant-client 1.19 上，旧代码无条件调 ``client.search()`` 会抛
    AttributeError，被 except 吞掉后返回空列表——不报错、只是永远 0 条。
    这条用例就是那个「永远 0 条」的哨兵。
    """
    pytest.importorskip("qdrant_client", reason=_SKIP_REASON)
    store = _in_memory_store(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        doc = pathlib.Path(tmp) / "rag.md"
        doc.write_text(
            "# RAG\n\nRabbitAgent 的三段式检索包括召回、重排与压缩三个阶段。\n" * 4,
            encoding="utf-8",
        )
        chunks = rag_pipeline.load_and_chunk_texts(
            paths=[str(doc)], chunk_size=120, chunk_overlap=20
        )
        rag_pipeline.index_chunks(store=store, chunks=chunks, rag_namespace="probe")

    assert len(chunks) > 0, "分片结果为空，用例自身失效"
    hits = rag_pipeline.search_vectors(
        store=store, query="三段式", top_k=5, rag_namespace="probe"
    )
    assert hits, "索引成功却检索不到任何结果——检索链路静默失效了"


def test_search_prefers_query_points_when_available(monkeypatch):
    """新客户端优先走 ``query_points``；只有旧客户端才回退 ``search``。"""
    pytest.importorskip("qdrant_client", reason=_SKIP_REASON)
    from qdrant_client import QdrantClient as _RealClient

    store = _in_memory_store(monkeypatch)
    assert hasattr(store.client, "query_points"), (
        "当前安装的 qdrant-client 应提供 query_points"
    )

    calls: list[str] = []

    def _fake_query_points(**kwargs):
        calls.append("query_points")
        raise RuntimeError("stop here")

    monkeypatch.setattr(store.client, "query_points", _fake_query_points, raising=True)
    store.search_similar(query_vector=[0.0] * 384, limit=1)
    assert calls == ["query_points"], "应优先使用 query_points 而非已移除的 search"
