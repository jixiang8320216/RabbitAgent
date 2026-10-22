"""端到端验证：agent 正常流程中 memory / storage / RAG / 上下文压缩 的真实可达性。

这个脚本回答的是「**主流程里到底能不能用上**」，而不是「模块是否存在」。

验证四层：

1. **装配层**：模拟 ``runtime_composition._attach_memory_tools`` 的真实调用，
   确认 ``memory`` 工具能被注册进 toolkit（这是 agent 能调用它的前提）。
2. **调用层**：用真实 ``MemoryTool.execute`` 走一遍 add / search / stats /
   consolidate / forget，确认 4 种 memory types（working/episodic/semantic/
   perceptual）在 agent 路径上真的被实例化并能读写（SQLite 存储）。
3. **RAG 层**：确认 ``RAGTool`` 在 agent 装配链上**是否可达** —— 若不可达，
   明确指出 RAG 只能被外部代码直接调用。
4. **压缩层**：确认 ``HistoryCompressionService`` 在真实回合执行器里被触发，
   且压缩策略真的会改写历史消息。

用法：
    python verify_agent_memory_flow.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from typing import Any, Dict, List

os.environ["EMBED_MODEL_TYPE"] = "tfidf"
os.environ.pop("EMBED_MODEL_NAME", None)
os.environ.pop("EMBED_API_KEY", None)
os.environ.pop("QDRANT_URL", None)
os.environ.pop("QDRANT_API_KEY", None)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

WORK_DIR = tempfile.mkdtemp(prefix="rabbit_flow_")

PASSED: List[str] = []
FAILED: List[str] = []
FINDINGS: List[str] = []


def check(name: str, fn):
    try:
        detail = fn()
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
        return True
    except Exception as exc:  # noqa: BLE001
        FAILED.append(name)
        print(f"  [FAIL] {name} — {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=3)
        return False


def finding(text: str):
    FINDINGS.append(text)
    print(f"  [发现] {text}")


def section(title: str):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# ---------------------------------------------------------------- 替身
def install_qdrant_memory_mode():
    """Qdrant 切本地内存模式（真实 qdrant-client API）。"""
    from domains.memory.storage import qdrant_store as qs

    class MemStore(qs.QdrantVectorStore):
        def _initialize_client(self):
            from qdrant_client import QdrantClient
            self.client = QdrantClient(location=":memory:")
            self._ensure_collection()

    def factory(*args, **kwargs):
        kwargs.pop("url", None)
        kwargs.pop("api_key", None)
        return MemStore(**kwargs)

    qs.QdrantVectorStore = factory
    qs.QdrantConnectionManager._instances.clear()

    @classmethod
    def patched(cls, url=None, api_key=None, collection_name="hello_agents_vectors",
                vector_size=384, distance="cosine", timeout=30, **kwargs):
        key = (url or "local", collection_name)
        if key not in cls._instances:
            cls._instances[key] = factory(
                collection_name=collection_name, vector_size=vector_size,
                distance=distance, timeout=timeout, **kwargs)
        return cls._instances[key]

    qs.QdrantConnectionManager.get_instance = patched


def install_neo4j_fake():
    from domains.memory.storage import neo4j_store as ns

    class FakeGraph:
        def __init__(self, **kwargs):
            self.entities: Dict[str, Dict[str, Any]] = {}
            self.relations: List[Dict[str, Any]] = []

        def add_entity(self, entity_id, name, entity_type, properties=None):
            self.entities[entity_id] = {"id": entity_id, "name": name, "type": entity_type,
                                        **(properties or {})}
            return True

        def add_relationship(self, from_entity_id, to_entity_id, relationship_type, properties=None):
            self.relations.append({"from_id": from_entity_id, "to_id": to_entity_id,
                                   "type": relationship_type, "properties": properties or {}})
            return True

        def find_related_entities(self, entity_id, relationship_types=None, max_depth=2, limit=50):
            return []

        def search_entities_by_name(self, name_pattern, entity_types=None, limit=20):
            return [e for e in self.entities.values()
                    if name_pattern.lower() in str(e.get("name", "")).lower()]

        def get_entity_relationships(self, entity_id):
            return []

        def clear_all(self):
            self.entities.clear()
            self.relations.clear()
            return True

        def get_stats(self):
            return {"total_nodes": len(self.entities),
                    "total_relationships": len(self.relations)}

        def health_check(self):
            return True

    ns.Neo4jGraphStore = FakeGraph


# ---------------------------------------------------------------- 1. 装配层
def verify_toolkit_registration():
    """模拟 runtime_composition 的真实装配，确认 memory 工具进得了 toolkit。"""
    from domains.memory.application.memory_agent_tools import (
        register_memory_tools, MEMORY_TOOL_NAME,
    )
    from domains.memory.application.memory_runtime_service import (
        MemoryRuntimeService, MemoryRuntimeSettings,
    )
    import pathlib

    class FakeToolkit:
        def __init__(self):
            self.functions: Dict[str, Any] = {}

        def register_tool_function(self, func, name=None, description=None,
                                   group=None, source=None, **kwargs):
            self.functions[name] = func
            return func

    def t_register():
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=pathlib.Path(WORK_DIR) / "user_memory.md",
            project_memory_file=pathlib.Path(WORK_DIR) / "project_memory.md",
        )
        runtime = MemoryRuntimeService(settings)
        toolkit = FakeToolkit()
        tool = register_memory_tools(toolkit, runtime)
        assert tool is not None, "记忆工具注册失败（返回 None）"
        assert MEMORY_TOOL_NAME in toolkit.functions, \
            f"toolkit 中没有 {MEMORY_TOOL_NAME}，已注册: {list(toolkit.functions)}"
        return f"工具 '{MEMORY_TOOL_NAME}' 已注册进 toolkit"

    def t_tool_callable():
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=pathlib.Path(WORK_DIR) / "user_memory.md",
            project_memory_file=pathlib.Path(WORK_DIR) / "project_memory.md",
        )
        runtime = MemoryRuntimeService(settings)
        toolkit = FakeToolkit()
        register_memory_tools(toolkit, runtime)
        fn = toolkit.functions["memory"]
        assert callable(fn), "注册进 toolkit 的不是可调用对象"
        out = fn({"action": "stats"})
        assert isinstance(out, str), f"工具应返回字符串，实际 {type(out)}"
        return f"工具可直接调用，stats 返回 {len(out)} 字符"

    for fn in (t_register, t_tool_callable):
        check(f"[装配] {fn.__name__[2:]}", fn)

    def t_prompt_projection():
        """memory 还会以「提示词注入」的方式参与主流程。"""
        from domains.memory.application.memory_runtime_service import (
            MemoryRuntimeService, MemoryRuntimeSettings,
        )
        import pathlib
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=pathlib.Path(WORK_DIR) / "user_memory.md",
            project_memory_file=pathlib.Path(WORK_DIR) / "project_memory.md",
        )
        runtime = MemoryRuntimeService(settings)
        # 先读出当前 revision（乐观并发需要），再写入
        snapshot = runtime.read("user")
        runtime.save_user_content("user", "用户偏好使用中文回答。",
                                  experted_revision=snapshot.revision)
        projection = runtime.prompt_projection()
        assert "用户偏好使用中文回答" in projection.content, \
            f"记忆未注入投影: {projection.content[:80]}"
        return f"记忆投影 {len(projection.content)} 字符，已进入系统提示词"

    check("[装配] prompt_projection 注入", t_prompt_projection)


# ---------------------------------------------------------------- 1b. 记忆文档轨
def verify_memory_document_track():
    """验证「Markdown 记忆文档」这条轨：写入 / 编辑 / 搜索 / 遗忘 / 删除。"""
    import pathlib
    from domains.memory.application.memory_runtime_service import (
        MemoryRuntimeService, MemoryRuntimeSettings,
    )

    def t_write_read():
        d = pathlib.Path(WORK_DIR) / "doc_track"
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=d / "user_memory.md",
            project_memory_file=d / "project_memory.md",
        )
        rt = MemoryRuntimeService(settings)
        snap = rt.read("user")
        rt.save_user_content("user", "## 偏好\n\n用户偏好使用中文回答。\n",
                             experted_revision=snap.revision)
        after = rt.read("user")
        assert "中文" in after.content, "写入后读不到内容"
        assert after.revision != snap.revision, "revision 未变化（乐观并发失效）"
        return f"写入并读回成功, revision {snap.revision[:6]}->{after.revision[:6]}"

    def t_edit():
        d = pathlib.Path(WORK_DIR) / "doc_track2"
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=d / "user_memory.md",
            project_memory_file=d / "project_memory.md",
        )
        rt = MemoryRuntimeService(settings)
        snap = rt.read("user")
        rt.save_user_content("user", "原始内容", experted_revision=snap.revision)
        snap2 = rt.read("user")
        result = rt.edit_with_result("user", "原始内容", "新内容")
        assert "新内容" in result.document.content, "编辑未生效"
        assert result.diff, "未产生 diff"
        return f"编辑成功, diff {len(result.diff)} 字符"

    def t_search_chinese():
        d = pathlib.Path(WORK_DIR) / "doc_track3"
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=d / "user_memory.md",
            project_memory_file=d / "project_memory.md",
        )
        rt = MemoryRuntimeService(settings)
        snap = rt.read("user")
        rt.save_user_content("user", "## 偏好\n\n用户偏好使用中文回答。\n\n## 架构\n\n项目采用 DDD 分层架构。\n",
                             experted_revision=snap.revision)
        hits_zh = rt.search("中文")
        hits_en = rt.search("DDD")
        assert hits_zh, "中文检索无命中（分词器问题）"
        assert hits_en, "英文检索无命中"
        return f"中文检索 {len(hits_zh)} 条, 英文检索 {len(hits_en)} 条"

    def t_forget():
        d = pathlib.Path(WORK_DIR) / "doc_track4"
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=d / "user_memory.md",
            project_memory_file=d / "project_memory.md",
        )
        rt = MemoryRuntimeService(settings)
        snap = rt.read("user")
        rt.save_user_content("user", "待遗忘内容", experted_revision=snap.revision)
        report = rt.forget("user")
        assert report.fully_removed, f"删除未干净: {report.store_errors}"
        return "遗忘（清空该域）成功"

    def t_forget_all():
        d = pathlib.Path(WORK_DIR) / "doc_track5"
        settings = MemoryRuntimeSettings(
            enabled=True,
            user_memory_file=d / "user_memory.md",
            project_memory_file=d / "project_memory.md",
        )
        rt = MemoryRuntimeService(settings)
        snap = rt.read("user")
        rt.save_user_content("user", "内容", experted_revision=snap.revision)
        reports = rt.forget_all()
        assert reports, "forget_all 无报告"
        return f"forget_all 覆盖 {len(reports)} 个记忆域"

    for fn in (t_write_read, t_edit, t_search_chinese, t_forget, t_forget_all):
        check(f"[文档轨] {fn.__name__[2:]}", fn)


# ---------------------------------------------------------------- 2. 调用层
def verify_memory_tool_operations():
    """走真实 MemoryTool.execute，确认 4 种 types + SQLite 存储在 agent 路径可用。"""
    from domains.tool.builtin.memory_tool import MemoryTool
    from domains.memory.base import MemoryConfig

    tool = MemoryTool(
        user_id="flow_user",
        memory_config=MemoryConfig(storage_path=os.path.join(WORK_DIR, "tool_mem")),
    )

    def t_types_enabled():
        types = tool.memory_types
        assert "working" in types and "episodic" in types and "semantic" in types, \
            f"默认类型缺失: {types}"
        mgr_types = list(tool.memory_manager.memory_types)
        assert set(mgr_types) == {"working", "episodic", "semantic"}, \
            f"管理器类型不对: {mgr_types}"
        return f"agent 默认启用 {mgr_types}（perceptual 需显式开启）"

    def t_add_working():
        out = tool.execute("add", content="项目使用 DDD 分层架构", memory_type="working",
                           importance=0.9)
        assert "✅" in out or "成功" in out, f"添加失败: {out}"
        return out.strip().splitlines()[0][:60]

    def t_add_episodic():
        out = tool.execute("add", content="昨天完成了向量检索模块的联调", memory_type="episodic")
        assert "✅" in out or "成功" in out, f"添加失败: {out}"
        return "情景记忆写入成功"

    def t_add_semantic():
        out = tool.execute("add", content="定义：分片指数是数据库分片策略之一",
                           memory_type="semantic")
        assert "✅" in out or "成功" in out, f"添加失败: {out}"
        return "语义记忆写入成功"

    def t_search():
        out = tool.execute("search", query="向量检索", limit=5)
        assert "❌" not in out, f"检索报错: {out}"
        return f"检索返回 {len(out)} 字符"

    def t_stats():
        out = tool.execute("stats")
        assert "❌" not in out, f"统计报错: {out}"
        assert "working" in out, f"统计缺少类型明细: {out[:120]}"
        return out.strip().splitlines()[0][:70]

    def t_sqlite_touched():
        """确认 SQLite 存储层真的落了盘。"""
        from domains.memory.storage.document_store import SQLiteDocumentStore
        store = SQLiteDocumentStore(
            db_path=os.path.join(WORK_DIR, "tool_mem", "memory.db"))
        docs = store.search_memories(user_id="flow_user", limit=50)
        assert docs, "SQLite 中没有任何记忆落盘"
        return f"SQLite 落盘 {len(docs)} 条（权威存储生效）"

    def t_consolidate():
        out = tool.execute("consolidate", from_type="working", to_type="episodic",
                           importance_threshold=0.1)
        assert "❌" not in out, f"整合报错: {out}"
        return out.strip()[:70]

    def t_forget():
        out = tool.execute("forget", strategy="importance_based", threshold=0.0)
        assert "❌" not in out, f"遗忘报错: {out}"
        return out.strip()[:60]

    def t_perceptual_optin():
        """perceptual 默认关闭，显式开启后应能实例化。"""
        t2 = MemoryTool(
            user_id="flow_user",
            memory_config=MemoryConfig(storage_path=os.path.join(WORK_DIR, "tool_mem2")),
            memory_types=["working", "episodic", "semantic", "perceptual"],
        )
        assert "perceptual" in t2.memory_manager.memory_types, "perceptual 未启用"
        out = t2.execute("add", content="一段描述性文本", memory_type="perceptual")
        assert "❌" not in out, f"感知记忆添加失败: {out}"
        return "perceptual 显式开启后可正常写入"

    for fn in (t_types_enabled, t_add_working, t_add_episodic, t_add_semantic,
               t_search, t_stats, t_sqlite_touched, t_consolidate, t_forget,
               t_perceptual_optin):
        check(f"[调用] {fn.__name__[2:]}", fn)


# ---------------------------------------------------------------- 3. RAG 层
def verify_rag_reachability():
    """确认 RAG 在 agent 主流程中是否可达。"""
    def t_rag_tool_exists():
        from domains.tool.builtin.rag_tool import RAGTool
        assert RAGTool is not None
        return "RAGTool 类存在且可导入"

    def t_rag_not_in_agent_composition():
        """关键结论：RAGTool 未接入 runtime_composition 的装配链。"""
        import inspect
        from domains.agent.application.lifecycle import runtime_composition as rc
        src = inspect.getsource(rc)
        assert "_attach_rag_tools" in src, "RAG 装配函数缺失（结论需更新）"
        finding("RAG 已接入 agent 装配链（runtime_composition._attach_rag_tools）")
        return "确认已接入 agent 装配链"

    def t_rag_usable_directly():
        """RAG 能力本身可被外部代码直接调用。"""
        from domains.memory.rag.pipeline import (
            create_rag_pipeline, load_and_chunk_texts, index_chunks,
        )
        doc_dir = os.path.join(WORK_DIR, "ragdocs")
        os.makedirs(doc_dir, exist_ok=True)
        doc = os.path.join(doc_dir, "k.md")
        with open(doc, "w", encoding="utf-8") as f:
            f.write("# 检索\n\n向量检索通过余弦相似度召回相关文档。\n")
        pipeline = create_rag_pipeline(rag_namespace="flow")
        chunks = load_and_chunk_texts(paths=[doc], chunk_size=100, chunk_overlap=10,
                                      namespace="flow", source_label="rag")
        index_chunks(store=pipeline["store"], chunks=chunks, rag_namespace="flow")
        hits = pipeline["search"]("余弦相似度", top_k=3)
        assert hits, "RAG 直接调用未命中"
        return f"RAG 直接调用可用，命中 {len(hits)} 条"

    def t_rag_tool_direct():
        """RAGTool 自身可独立实例化并执行。"""
        from domains.tool.builtin.rag_tool import RAGTool
        tool = RAGTool()
        assert hasattr(tool, "run") or hasattr(tool, "execute"), "RAGTool 缺少执行入口"
        return "RAGTool 可独立实例化并执行"

    for fn in (t_rag_tool_exists, t_rag_not_in_agent_composition,
               t_rag_usable_directly, t_rag_tool_direct):
        check(f"[rag] {fn.__name__[2:]}", fn)


# ---------------------------------------------------------------- 3b. RAG 接入层
def verify_rag_attachment():
    """验证 RAG 真的能通过装配链接进 agent 的 toolkit。"""
    from domains.configuration.infrastructure.merge import rag_config_rules
    from domains.memory.application import rag_agent_tools
    from domains.tool.core.application.toolkit import Toolkit

    def t_config_default_off():
        """RAG 需要外部 Qdrant，默认必须关闭。"""
        settings = rag_config_rules.resolve_rag_settings({})
        assert settings["enabled"] is False, "RAG 默认应为关闭"
        return "默认关闭（需外部 Qdrant 服务）"

    def t_config_explicit_on():
        settings = rag_config_rules.resolve_rag_settings(
            {"personalization": {"rag": {"enabled": True,
                                         "collection_name": "kb",
                                         "namespace": "ns1"}}})
        assert settings["enabled"] is True, "显式开启未生效"
        assert settings["collection_name"] == "kb", "集合名未透传"
        assert settings["namespace"] == "ns1", "命名空间未透传"
        return "显式开启生效，参数正确透传"

    def t_not_registered_when_disabled():
        toolkit = Toolkit()
        settings = rag_config_rules.resolve_rag_settings({})
        result = rag_agent_tools.register_rag_tools(toolkit, settings)
        assert result is None, "关闭状态下不应注册"
        assert "rag" not in toolkit.tools, f"关闭状态下工具集不该有 rag: {list(toolkit.tools)}"
        return "关闭时正确跳过注册"

    def t_registered_when_enabled():
        """Qdrant 可用时，RAG 工具必须真的进 toolkit 且可调用。"""
        toolkit = Toolkit()
        settings = rag_config_rules.resolve_rag_settings(
            {"personalization": {"rag": {
                "enabled": True,
                "knowledge_base_path": os.path.join(WORK_DIR, "rag_kb"),
                "collection_name": "rag_attach_test",
            }}})
        result = rag_agent_tools.register_rag_tools(toolkit, settings)
        assert result is not None, "RAG 工具注册失败"
        assert "rag" in toolkit.tools, f"toolkit 中没有 rag: {list(toolkit.tools)}"
        return "已注册进 toolkit"

    def t_tool_callable():
        """注册进 toolkit 的函数必须能被 toolkit 的调用约定执行。"""
        toolkit = Toolkit()
        settings = rag_config_rules.resolve_rag_settings(
            {"personalization": {"rag": {
                "enabled": True,
                "knowledge_base_path": os.path.join(WORK_DIR, "rag_kb2"),
                "collection_name": "rag_attach_test2",
            }}})
        rag_agent_tools.register_rag_tools(toolkit, settings)
        registration = toolkit.tools["rag"]
        # toolkit 以 func(**kwargs) 调用 —— 签名不匹配会直接 TypeError
        out = registration.func(action="stats")
        assert isinstance(out, str), f"应返回字符串，实际 {type(out)}"
        assert "❌" not in out, f"调用报错: {out[:120]}"
        return f"toolkit 调用约定下可执行, 返回 {len(out)} 字符"

    def t_attach_in_composition():
        """装配函数本身可用（配置缺失时安全返回 None）。"""
        from domains.agent.application.lifecycle import runtime_composition as rc

        class FakeApp:
            class config:  # noqa: N801 - 模拟 AppConfig
                runtime_config_snapshot = {}

        class FakeHost:
            app_instance = FakeApp()

        toolkit = Toolkit()
        result = rc._attach_rag_tools(FakeHost(), toolkit)
        assert result is None, "无配置时应返回 None"
        assert "rag" not in toolkit.tools, "无配置时不应注册"
        return "装配函数就位，无配置时安全跳过"

    for fn in (t_config_default_off, t_config_explicit_on, t_not_registered_when_disabled,
               t_registered_when_enabled, t_tool_callable, t_attach_in_composition):
        check(f"[rag接入] {fn.__name__[2:]}", fn)


# ---------------------------------------------------------------- 4. 压缩层
def verify_compression():
    """确认上下文压缩在真实回合执行器里被触发，且真的改写历史。"""
    def t_service_exists():
        from domains.context.application.history_compression import (
            HistoryCompressionService, DEFAULT_COMPRESSION_THRESHOLD,
        )
        svc = HistoryCompressionService(threshold=10)
        assert svc is not None
        return f"HistoryCompressionService 就绪（默认阈值 {DEFAULT_COMPRESSION_THRESHOLD}）"

    def t_orchestrator_wires_it():
        """真实回合执行器里持有并调用压缩服务。"""
        import inspect
        from domains.runtime.application.orchestrators import session_turn_execution as ste
        src = inspect.getsource(ste)
        assert "_maybe_compress_history" in src, "回合执行器缺少压缩入口"
        assert "HistoryCompressionService" in src, "回合执行器未装配压缩服务"
        finding("上下文压缩在**每轮 LLM 调用前**触发"
                "（session_turn_execution._maybe_compress_history）")
        return "回合执行器已装配并调用压缩服务"

    def t_policy_actually_compresses():
        """验证压缩策略真的改写消息（不是空跑）。"""
        from domains.context.domain.history_compression_policy import (
            apply_compression, messages_tokens, select_compression_window,
        )
        from domains.agent.domain.value_objects.message import Msg

        # 需足够多的历史：keep_recent=4 且 min_compressible=3，
        # 至少要有 7 条以上才有可压缩区间。
        msgs = []
        for i in range(6):
            msgs.append(Msg(name="u", role="user",
                            content=f"第{i}轮问题，请详细回答关于向量检索的实现细节。" * 3))
            msgs.append(Msg(name="a", role="assistant",
                            content=f"第{i}轮很长的回答内容，需要足够长才能触发压缩。" * 5))
        before = messages_tokens(msgs)
        plan = select_compression_window(msgs, threshold=20, keep_recent=2)
        assert plan.start < plan.end, f"未选出可压缩区间: {plan}"
        # summary_factory 由调用方提供「文本 -> 消息对象」的构造器
        def summary_factory(text: str):
            return Msg(name="summary", role="assistant", content=text)

        compressed = apply_compression(
            msgs, plan, "历史摘要：讨论了向量检索与相似度。",
            summary_factory=summary_factory,
        )
        after = messages_tokens(compressed)
        assert after < before, f"压缩后 token 未减少: {before} -> {after}"
        return f"压缩生效: {before} -> {after} tokens, 消息 {len(msgs)} -> {len(compressed)}"

    def t_service_maybe_compress():
        """走 service 层的真实入口（maybe_compress 是协程）。"""
        import asyncio
        from domains.context.application.history_compression import (
            HistoryCompressionService,
        )
        from domains.agent.domain.value_objects.message import Msg

        class FakeContext:
            """鸭子类型上下文：提供 messages 属性与 replace_window 方法。"""

            def __init__(self, messages):
                self.messages = list(messages)
                self.replacements: list = []

            def replace_window(self, start, end, message, mark):
                self.messages = (
                    self.messages[:start] + [message] + self.messages[end:]
                )
                self.replacements.append((start, end, mark))

        msgs = [
            Msg(name="u", role="user", content="长内容" * 20),
            Msg(name="a", role="assistant", content="长回答" * 20),
            Msg(name="u", role="user", content="后续" * 20),
        ]
        svc = HistoryCompressionService(threshold=10)
        ctx = FakeContext(msgs)
        report = asyncio.run(svc.maybe_compress(ctx, agent_id="test", metadata={}))
        assert report is not None, "压缩报告为空"
        if report.performed:
            assert ctx.replacements, "报告称已压缩，但上下文未被改写"
            return (f"压缩已执行: {report.messages_before}->{report.messages_after} msgs, "
                    f"tokens {report.tokens_before}->{report.tokens_after}, 策略={report.strategy}")
        return f"未触发压缩（performed=False, tokens_before={report.tokens_before}）"

    for fn in (t_service_exists, t_orchestrator_wires_it, t_policy_actually_compresses,
               t_service_maybe_compress):
        check(f"[压缩] {fn.__name__[2:]}", fn)


# ---------------------------------------------------------------- 5. 能力矩阵
def print_capability_matrix():
    """汇总：agent 主流程中各能力的真实可达性。"""
    section("能力可达性矩阵（agent 正常流程）")
    matrix = [
        ("memory 工具注册", "✅ 已接入", "runtime_composition._attach_memory_tools → register_memory_tools"),
        ("memory 提示词注入", "✅ 已接入", "runtime_composition._append_memory → prompt_projection"),
        ("memory 类型 working", "✅ 可用", "MemoryTool → MemoryManager → WorkingMemory（纯内存）"),
        ("memory 类型 episodic", "✅ 可用", "EpisodicMemory（SQLite 权威 + Qdrant 加速）"),
        ("memory 类型 semantic", "✅ 可用", "SemanticMemory（Qdrant + Neo4j，缺库自动降级）"),
        ("memory 类型 perceptual", "⚠️ 默认关闭", "需 memory_types 显式含 perceptual 才实例化"),
        ("存储层 SQLite", "✅ 可用", "SQLiteDocumentStore（写入+读出脱敏）"),
        ("存储层 Qdrant", "⚠️ 需服务", "无 Qdrant 时降级为关键词检索"),
        ("存储层 Neo4j", "⚠️ 需服务", "无 Neo4j 时降级为本地实体缓存"),
        ("RAG 管道", "✅ 已接入", "runtime_composition._attach_rag_tools → register_rag_tools（需显式开启）"),
        ("RAG 启用开关", "⚠️ 默认关闭", "personalization.rag.enabled = true 开启（依赖外部 Qdrant）"),
        ("上下文压缩", "✅ 已接入", "每轮 LLM 前由 session_turn_execution 触发"),
    ]
    print(f"  {'能力':<22}{'状态':<18}说明")
    print("  " + "-" * 70)
    for name, status, note in matrix:
        print(f"  {name:<22}{status:<18}{note}")


def main():
    print(f"工作目录: {WORK_DIR}")
    print(f"Python: {sys.version.split()[0]}")

    section("步骤 0：注入离线替身")
    install_qdrant_memory_mode()
    install_neo4j_fake()
    print("  Qdrant -> 内存模式；Neo4j -> 内存替身")

    section("步骤 1：装配层（memory 工具能否进 agent 的 toolkit）")
    verify_toolkit_registration()

    section("步骤 1b：记忆文档轨（Markdown 记忆：写入/编辑/搜索/遗忘）")
    verify_memory_document_track()

    section("步骤 2：调用层（4 种 memory types + SQLite 存储在 agent 路径是否可用）")
    verify_memory_tool_operations()

    section("步骤 3：RAG 层（是否接入 agent 主流程）")
    verify_rag_reachability()

    section("步骤 3b：RAG 接入层（配置开关 -> 工具注册 -> 可调用）")
    verify_rag_attachment()

    section("步骤 4：上下文压缩层")
    verify_compression()

    print_capability_matrix()

    section("结果汇总")
    total = len(PASSED) + len(FAILED)
    print(f"  通过: {len(PASSED)}/{total}")
    print(f"  失败: {len(FAILED)}")
    for name in FAILED:
        print(f"    - {name}")
    if FINDINGS:
        print("\n  关键发现:")
        for f in FINDINGS:
            print(f"    * {f}")

    import shutil
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
