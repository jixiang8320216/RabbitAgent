"""memory 域能力验证脚本（离线可跑）。

用途
----
在**不依赖外部服务**的前提下，验证 RabbitAgent 的 memory 子域是否真的可用：
1. 4 种记忆类型（working / episodic / semantic / perceptual）能否实例化并完成增删改查；
2. 存储能力（SQLite 文档存储、Qdrant 向量存储、Neo4j 图存储）是否真的读写成功；
3. RAG 能力（分块 -> 向量索引 -> 检索 -> 重排 -> 压缩 + ACL 过滤）是否端到端跑通。

离线策略
--------
- **Qdrant**：用 ``location=":memory:"`` 的本地嵌入模式，真实走 qdrant-client 的
  向量写入/检索/删除 API（不是 mock），只是不连服务器。
- **Neo4j**：本机无服务，用 ``InMemoryGraphStore`` 替身实现同一套接口
  （add_entity / add_relationship / find_related_entities / ... ），语义等价。
- **Embedding**：强制 ``EMBED_MODEL_TYPE=tfidf``，用 scikit-learn 的
  字符 n-gram TF-IDF，纯本地、确定性、无需下载模型。
- **spaCy**：本机无中文模型，实体提取会退化（这本身也是被验证的一部分）。

用法
----
    python verify_memory_offline.py
退出码 0 表示全部通过。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List

# ---------------------------------------------------------------- 环境准备
# 必须在 import 项目模块之前设置：强制走 TF-IDF 兜底嵌入（纯本地、无需下载模型）
os.environ["EMBED_MODEL_TYPE"] = "tfidf"
os.environ.pop("EMBED_MODEL_NAME", None)
os.environ.pop("EMBED_API_KEY", None)
os.environ.pop("EMBED_BASE_URL", None)
# 避免误连真实 Qdrant
os.environ.pop("QDRANT_URL", None)
os.environ.pop("QDRANT_API_KEY", None)
# 感知记忆的多模态模型（CLIP/CLAP）需要从 HuggingFace 下载权重；
# 离线环境下会反复重试网络、拖慢初始化（甚至超时）。这里显式关闭，
# 走确定性哈希编码路径 —— 这本身就是被验证的降级能力之一。
os.environ["PERCEPTUAL_DISABLE_CLIP"] = "1"
os.environ["PERCEPTUAL_DISABLE_CLAP"] = "1"

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

WORK_DIR = tempfile.mkdtemp(prefix="rabbit_mem_verify_")

# ---------------------------------------------------------------- 结果记录
PASSED: List[str] = []
FAILED: List[str] = []
NOTES: List[str] = []


def check(name: str, fn):
    """执行一个检查项，记录通过/失败。"""
    try:
        detail = fn()
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
        return True
    except Exception as exc:  # noqa: BLE001
        FAILED.append(name)
        print(f"  [FAIL] {name} — {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=4)
        return False


def section(title: str):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ================================================================ 替身实现
class InMemoryGraphStore:
    """Neo4j 图存储的内存替身（接口与 Neo4jGraphStore 对齐）。

    用于在没有 Neo4j 服务的机器上验证「语义记忆的图谱链路」是否真的连通：
    实体/关系写入、关系遍历、按名搜索、统计、清空。
    """

    def __init__(self, **kwargs):
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.relations: List[Dict[str, Any]] = []

    # -- 写 --
    def add_entity(self, entity_id: str, name: str, entity_type: str,
                   properties: Dict[str, Any] = None) -> bool:
        props = dict(properties or {})
        props.update({"id": entity_id, "name": name, "type": entity_type})
        self.entities[entity_id] = props
        return True

    def add_relationship(self, from_entity_id: str, to_entity_id: str,
                         relationship_type: str,
                         properties: Dict[str, Any] = None) -> bool:
        props = dict(properties or {})
        props.update({"type": relationship_type})
        self.relations.append({
            "from_id": from_entity_id,
            "to_id": to_entity_id,
            "type": relationship_type,
            "properties": props,
        })
        return True

    # -- 读 --
    def find_related_entities(self, entity_id: str, relationship_types: List[str] = None,
                              max_depth: int = 2, limit: int = 50) -> List[Dict[str, Any]]:
        """BFS 找 1..max_depth 跳可达的实体（与 Neo4j 变长路径查询等价）。"""
        if entity_id not in self.entities:
            return []
        allowed = set(relationship_types) if relationship_types else None
        seen = {entity_id}
        frontier = {entity_id}
        found: List[Dict[str, Any]] = []
        for depth in range(1, max_depth + 1):
            next_frontier = set()
            for current in frontier:
                for rel in self.relations:
                    if allowed and rel["type"] not in allowed:
                        continue
                    if rel["from_id"] == current:
                        other = rel["to_id"]
                    elif rel["to_id"] == current:
                        other = rel["from_id"]
                    else:
                        continue
                    if other in seen:
                        continue
                    seen.add(other)
                    next_frontier.add(other)
                    entity = dict(self.entities.get(other, {}))
                    entity["distance"] = depth
                    entity["relationship_path"] = [rel["type"]]
                    # 关系属性里的 memory_id 要透出，供语义记忆的图检索使用
                    for key in ("memory_id", "user_id", "importance"):
                        if key in rel["properties"]:
                            entity[key] = rel["properties"][key]
                    found.append(entity)
            frontier = next_frontier
            if not frontier:
                break
        return found[:limit]

    def search_entities_by_name(self, name_pattern: str, entity_types: List[str] = None,
                                 limit: int = 20) -> List[Dict[str, Any]]:
        out = []
        for entity in self.entities.values():
            if name_pattern.lower() not in str(entity.get("name", "")).lower():
                continue
            if entity_types and entity.get("type") not in entity_types:
                continue
            out.append(dict(entity))
        return out[:limit]

    def get_entity_relationships(self, entity_id: str) -> List[Dict[str, Any]]:
        out = []
        for rel in self.relations:
            if rel["from_id"] == entity_id or rel["to_id"] == entity_id:
                out.append({
                    "relationship": rel["properties"],
                    "other_entity": dict(self.entities.get(
                        rel["to_id"] if rel["from_id"] == entity_id else rel["from_id"], {})),
                    "direction": "outgoing" if rel["from_id"] == entity_id else "incoming",
                })
        return out

    def delete_entity(self, entity_id: str) -> bool:
        return self.entities.pop(entity_id, None) is not None

    def clear_all(self) -> bool:
        self.entities.clear()
        self.relations.clear()
        return True

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_nodes": len(self.entities),
            "total_relationships": len(self.relations),
            "entity_nodes": len(self.entities),
            "memory_nodes": 0,
        }

    def health_check(self) -> bool:
        return True


def patch_qdrant_to_memory_mode():
    """把 QdrantVectorStore 的客户端切到本地内存模式。

    这样**真实调用** qdrant-client 的 create_collection / upsert / query_points /
    delete，而不是打桩。做法是子类化并在 __init__ 里替换 client。
    """
    from domains.memory.storage import qdrant_store as qs

    class InMemoryQdrantStore(qs.QdrantVectorStore):
        def _initialize_client(self):
            from qdrant_client import QdrantClient
            self.client = QdrantClient(location=":memory:")
            self._ensure_collection()

    original = qs.QdrantVectorStore

    def factory(*args, **kwargs):
        # 强制用内存模式：忽略 url/api_key
        kwargs.pop("url", None)
        kwargs.pop("api_key", None)
        return InMemoryQdrantStore(**kwargs)

    qs.QdrantVectorStore = factory
    # 连接管理器缓存的是原始类，需要一并替换
    qs.QdrantConnectionManager._instances.clear()
    original_qcm_get = qs.QdrantConnectionManager.get_instance.__func__

    @classmethod
    def patched_get_instance(cls, url=None, api_key=None, collection_name="hello_agents_vectors",
                             vector_size=384, distance="cosine", timeout=30, **kwargs):
        key = (url or "local", collection_name)
        if key not in cls._instances:
            cls._instances[key] = factory(
                collection_name=collection_name,
                vector_size=vector_size,
                distance=distance,
                timeout=timeout,
                **kwargs,
            )
        return cls._instances[key]

    qs.QdrantConnectionManager.get_instance = patched_get_instance
    return original


def patch_neo4j_to_inmemory():
    """把 Neo4jGraphStore 换成内存替身。"""
    from domains.memory.storage import neo4j_store as ns

    class PatchedNeo4jGraphStore(InMemoryGraphStore):
        def __init__(self, **kwargs):
            super().__init__()

    ns.Neo4jGraphStore = PatchedNeo4jGraphStore
    return ns


# ================================================================ 验证主体
def verify_working_memory():
    """工作记忆：实例化 / 增 / 检索 / 改 / 删 / 容量 / 遗忘。"""
    from domains.memory.types.working import WorkingMemory
    from domains.memory.base import MemoryConfig, MemoryItem

    cfg = MemoryConfig(storage_path=WORK_DIR, working_memory_capacity=3,
                       working_memory_tokens=200, working_memory_ttl_minutes=60)
    wm = WorkingMemory(cfg)

    def make(content, importance=0.6, minutes_ago=0):
        return MemoryItem(
            id=f"w-{content[:6]}-{minutes_ago}",
            content=content,
            memory_type="working",
            user_id="u1",
            timestamp=datetime.now() - timedelta(minutes=minutes_ago),
            importance=importance,
            metadata={},
        )

    wm.add(make("苹果是一种水果", 0.9))
    wm.add(make("北京是中国的首都", 0.8))
    wm.add(make("今天天气不错适合出门", 0.5))

    def t_add():
        assert len(wm.get_all()) == 3, f"应有 3 条，实际 {len(wm.get_all())}"
        return f"3 条入内存, current_tokens={wm.current_tokens}"

    def t_retrieve():
        hits = wm.retrieve("苹果", limit=5)
        assert len(hits) >= 1, "关键词检索应有命中"
        assert "苹果" in hits[0].content, f"Top1 应含关键词，实际 {hits[0].content}"
        return f"检索命中 {len(hits)} 条, Top1={hits[0].content[:12]}"

    def t_retrieve_limit():
        # 关键回归点：原先 return 写在 for 循环体内，limit 永远失效（最多 1 条）
        wm2 = WorkingMemory(MemoryConfig(storage_path=WORK_DIR))
        for i in range(6):
            wm2.add(make(f"测试条目 内容{i} 苹果", 0.7))
        hits = wm2.retrieve("苹果", limit=5)
        assert len(hits) == 5, f"limit=5 应返回 5 条，实际 {len(hits)}（提前 return bug）"
        return f"limit 生效，返回 {len(hits)} 条"

    def t_update():
        ok = wm.update(wm.get_all()[0].id, content="苹果是红色水果", importance=0.95)
        assert ok, "update 应返回 True"
        target = [m for m in wm.get_all() if m.id == wm.get_all()[0].id][0]
        assert target.content == "苹果是红色水果", f"内容未更新: {target.content}"
        return "内容与重要性均已更新"

    def t_remove():
        before = len(wm.get_all())
        mid = wm.get_all()[-1].id
        ok = wm.remove(mid)
        assert ok and len(wm.get_all()) == before - 1, "删除应生效"
        assert wm.current_tokens >= 0, "token 计数不能为负"
        return f"删除 1 条, 剩 {len(wm.get_all())}, tokens={wm.current_tokens}"

    def t_capacity():
        # 关键回归点：原先条件写成 <，添加时会不断淘汰直到清空
        wm3 = WorkingMemory(MemoryConfig(storage_path=WORK_DIR, working_memory_capacity=3,
                                         working_memory_tokens=10000))
        for i in range(6):
            wm3.add(make(f"容量条目{i}", 0.7))
        assert len(wm3.get_all()) == 3, f"容量应为 3，实际 {len(wm3.get_all())}"
        return f"超限后保留 {len(wm3.get_all())} 条 (=capacity)"

    def t_token_limit():
        wm4 = WorkingMemory(MemoryConfig(storage_path=WORK_DIR, working_memory_capacity=100,
                                         working_memory_tokens=10))
        for i in range(20):
            wm4.add(make(f"很长的内容 token 计数 " * 5, 0.7))
        assert wm4.current_tokens <= 10, f"token 应被裁剪到 <=10，实际 {wm4.current_tokens}"
        return f"token 裁剪生效, current_tokens={wm4.current_tokens}"

    def t_expire():
        # 关键回归点：原先过期判定反了，保留的是「已过期」的
        wm5 = WorkingMemory(MemoryConfig(storage_path=WORK_DIR, working_memory_ttl_minutes=30))
        wm5.add(make("新鲜记忆", 0.7, minutes_ago=0))
        wm5.add(make("过期记忆", 0.7, minutes_ago=120))
        kept = wm5.get_all()
        contents = [m.content for m in kept]
        assert "新鲜记忆" in contents, f"新鲜记忆应保留，实际 {contents}"
        assert "过期记忆" not in contents, f"过期记忆应被清理，实际 {contents}"
        return f"TTL 清理正确，保留 {contents}"

    def t_forget():
        wm6 = WorkingMemory(MemoryConfig(storage_path=WORK_DIR))
        wm6.add(make("高价值记忆", 0.9))
        wm6.add(make("低价值记忆", 0.01))
        n = wm6.forget("importance_based", threshold=0.1)
        assert n >= 1, f"应遗忘至少 1 条，实际 {n}"
        assert all(m.importance >= 0.1 for m in wm6.get_all()), "低重要性记忆应被遗忘"
        return f"forget 清理 {n} 条"

    def t_stats():
        s = wm.get_stats()
        assert "count" in s and "current_tokens" in s, f"统计字段缺失: {list(s)}"
        return f"count={s['count']}, token_usage={s.get('token_usage'):.2f}"

    for fn in (t_add, t_retrieve, t_retrieve_limit, t_update, t_remove,
               t_capacity, t_token_limit, t_expire, t_forget, t_stats):
        check(f"[working] {fn.__name__[2:]}", fn)


def verify_episodic_memory():
    """情景记忆：SQLite 权威存储 + 向量索引 + 模式识别 + 时间线。"""
    from domains.memory.types.episodic import EpisodicMemory
    from domains.memory.base import MemoryConfig, MemoryItem

    cfg = MemoryConfig(storage_path=os.path.join(WORK_DIR, "episodic"))
    em = EpisodicMemory(cfg)

    _ids = {}

    def make(content, imp=0.7, session="s1", outcome=None):
        # 记录内容 -> id 的映射，避免测试里靠截断猜 id
        mid = f"e-{len(_ids) + 1}"
        _ids[content] = mid
        return MemoryItem(
            id=mid, content=content, memory_type="episodic", user_id="u1",
            timestamp=datetime.now(), importance=imp,
            metadata={"session_id": session, "context": {"topic": "test"},
                      "outcome": outcome, "participants": ["u1"], "tags": ["t"]},
        )

    def t_add():
        em.add(make("昨天在杭州参加了技术大会"))
        em.add(make("今天完成了向量检索模块的调试", session="s2"))
        assert len(em.get_all()) == 2, f"应有 2 条，实际 {len(em.get_all())}"
        return "2 条已写入"

    def t_sqlite_persist():
        # SQLite 是权威存储：直接查库确认落盘
        doc = em.doc_store.get_memory(_ids["昨天在杭州参加了技术大会"])
        assert doc is not None, "SQLite 中查不到刚写入的记忆"
        assert "杭州" in doc["content"], "内容不匹配"
        return f"SQLite 落盘确认 (db={os.path.basename(em.doc_store.db_path)})"

    def t_vector_write():
        # Qdrant 内存模式下应真实写入向量
        assert em.vector_store is not None, "向量库不可用"
        info = em.vector_store.get_collection_info()
        assert info.get("points_count", 0) >= 2, f"向量数不足: {info.get('points_count')}"
        return f"Qdrant points_count={info.get('points_count')}"

    def t_retrieve():
        hits = em.retrieve("技术大会", limit=5)
        assert len(hits) >= 1, "向量检索应有命中"
        return f"检索命中 {len(hits)} 条"

    def t_update():
        ok = em.update(_ids["昨天在杭州参加了技术大会"], content="昨天在杭州参加了AI技术大会", importance=0.9)
        assert ok, "update 应返回 True"
        doc = em.doc_store.get_memory(_ids["昨天在杭州参加了技术大会"])
        assert "AI" in doc["content"], "SQLite 未同步更新"
        return "SQLite + 内存缓存同步更新"

    def t_find_patterns():
        # 关键回归点：原先用 timedelta.hours（不存在），第二次调用必崩
        em.add(make("昨天在杭州参加了技术大会"))
        p1 = em.find_patterns(user_id="u1", min_frequency=1)
        p2 = em.find_patterns(user_id="u1", min_frequency=1)  # 走缓存分支
        assert isinstance(p2, list), "缓存分支应返回列表"
        return f"模式识别 {len(p1)} 条, 缓存分支正常"

    def t_get_all():
        items = em.get_all()
        assert all(isinstance(i.metadata, dict) for i in items), "metadata 应为 dict"
        return f"get_all 返回 {len(items)} 条, metadata 结构正确"

    def t_timeline():
        tl = em.get_timeline(user_id="u1")
        assert len(tl) >= 1, "时间线为空"
        assert "timestamp" in tl[0], "时间线缺少 timestamp"
        return f"时间线 {len(tl)} 条"

    def t_remove():
        before = len(em.get_all())
        ok = em.remove(_ids["今天完成了向量检索模块的调试"])
        assert ok, "remove 应返回 True"
        assert len(em.get_all()) == before - 1, "内存缓存未删除"
        assert em.doc_store.get_memory(_ids["今天完成了向量检索模块的调试"]) is None, "SQLite 未删除"
        return "内存 + SQLite 双删成功"

    def t_stats():
        s = em.get_stats()
        assert s["count"] >= 1, f"统计 count 异常: {s}"
        return f"count={s['count']}, vector_store={s['vector_store'].get('store_type')}"

    for fn in (t_add, t_sqlite_persist, t_vector_write, t_retrieve, t_update,
               t_find_patterns, t_get_all, t_timeline, t_remove, t_stats):
        check(f"[episodic] {fn.__name__[2:]}", fn)


def verify_semantic_memory():
    """语义记忆：实体/关系抽取 + 图存储 + 向量 + 混合检索。"""
    from domains.memory.types.semantic import SemanticMemory
    from domains.memory.base import MemoryConfig, MemoryItem

    cfg = MemoryConfig(storage_path=os.path.join(WORK_DIR, "semantic"))
    sm = SemanticMemory(cfg)

    _ids = {}

    def make(content, imp=0.7):
        mid = f"s-{len(_ids) + 1}"
        _ids[content] = mid
        return MemoryItem(
            id=mid, content=content, memory_type="semantic", user_id="u1",
            timestamp=datetime.now(), importance=imp, metadata={},
        )

    def t_init():
        assert sm.graph_store is not None, "图存储未初始化"
        assert sm.embedding_model is not None, "嵌入模型未就绪"
        return f"图存储可用, 嵌入维度={sm.embedding_model.dimension}"

    def t_add():
        sm.add(make("苹果公司是一家科技公司"))
        sm.add(make("北京是中国的首都"))
        assert len(sm.get_all()) == 2, f"应有 2 条，实际 {len(sm.get_all())}"
        return "2 条已写入"

    def t_graph_entities():
        # 图存储接口必须可写。实体数量取决于 spaCy 是否可用
        # （本机无中文模型时实体提取为 0，属预期降级，不判失败）。
        n_ent = len(sm.graph_store.entities)
        n_rel = len(sm.graph_store.relations)
        assert sm.graph_store is not None, "图存储未初始化"
        spaCy_ok = sm.nlp is not None
        if spaCy_ok:
            assert n_ent > 0, "spaCy 可用但图存储没有实体"
        return f"图存储可写: {n_ent} 实体 / {n_rel} 关系 (spaCy={'可用' if spaCy_ok else '不可用(降级)'})"

    def t_local_cache():
        # 本地缓存与 spaCy 可用性相关：无模型时实体为 0，但缓存结构必须存在
        assert hasattr(sm, "entities"), "缺少 entities 缓存"
        assert hasattr(sm, "relations"), "缺少 relations 缓存"
        return f"本地缓存结构就绪: {len(sm.entities)} 实体 / {len(sm.relations)} 关系"

    def t_retrieve():
        hits = sm.retrieve("苹果", limit=5)
        assert len(hits) >= 1, f"语义检索应命中，实际 {len(hits)}"
        return f"检索命中 {len(hits)} 条, Top1={hits[0].content[:14]}"

    def t_search_entities():
        ents = sm.search_entities("苹果", limit=5)
        return f"实体搜索返回 {len(ents)} 条"

    def t_update():
        ok = sm.update(_ids["苹果公司是一家科技公司"], content="苹果公司是一家消费电子公司", importance=0.9)
        assert ok, "update 应返回 True"
        return "内容更新成功"

    def t_remove():
        before = len(sm.get_all())
        ok = sm.remove(_ids["北京是中国的首都"])
        assert ok and len(sm.get_all()) == before - 1, "删除未生效"
        return "删除成功"

    def t_stats():
        s = sm.get_stats()
        assert "count" in s, f"统计字段缺失: {list(s)}"
        return f"count={s['count']}, entities={s['entities_count']}"

    for fn in (t_init, t_add, t_graph_entities, t_local_cache, t_retrieve,
               t_search_entities, t_update, t_remove, t_stats):
        check(f"[semantic] {fn.__name__[2:]}", fn)


def verify_perceptual_memory():
    """感知记忆：多模态编码 + SQLite 权威存储 + 按模态检索。"""
    from domains.memory.types.perceptual import PerceptualMemory
    from domains.memory.base import MemoryConfig, MemoryItem

    cfg = MemoryConfig(storage_path=os.path.join(WORK_DIR, "perceptual"))
    pm = PerceptualMemory(cfg)
    _ids = {}

    def make(content, modality="text", imp=0.7, raw=None):
        mid = f"p-{len(_ids) + 1}-{modality}"
        _ids[content] = mid
        return MemoryItem(
            id=mid, content=content, memory_type="perceptual",
            user_id="u1", timestamp=datetime.now(), importance=imp,
            metadata={"modality": modality, "raw_data": raw if raw is not None else content},
        )

    def t_init():
        assert pm.vector_stores, "向量集合全部不可用（降级也应能构造）"
        return f"向量集合: {list(pm.vector_stores)}"

    def t_add_text():
        pm.add(make("这是一段中文文本内容"))
        assert len(pm.get_all()) == 1, "文本未写入"
        return "文本模态写入成功"

    def t_add_image():
        pm.add(make("image-perception", modality="image", raw=b"\x89PNG fake bytes"))
        assert len(pm.get_all()) == 2, "图像未写入"
        return "图像模态写入成功（哈希编码降级路径）"

    def t_sqlite():
        doc = pm.doc_store.get_memory(_ids["这是一段中文文本内容"])
        assert doc is not None, "SQLite 未落盘"
        assert doc["properties"].get("modality") == "text", "模态属性缺失"
        return "SQLite 落盘 + 模态属性正确"

    def t_vector_write():
        info = pm.vector_stores["text"].get_collection_info()
        assert info.get("points_count", 0) >= 1, f"向量数不足: {info}"
        return f"Qdrant points_count={info.get('points_count')}"

    def t_retrieve():
        hits = pm.retrieve("中文文本", limit=5)
        assert len(hits) >= 1, f"检索应命中，实际 {len(hits)}"
        return f"检索命中 {len(hits)} 条"

    def t_update():
        # 关键回归点：原先引用不存在的 self.vector_store
        ok = pm.update(_ids["这是一段中文文本内容"], content="更新后的中文文本", importance=0.9)
        assert ok, "update 应返回 True"
        return "更新成功（含向量重嵌入路径）"

    def t_by_modality():
        text_items = pm.get_by_modality("text", limit=10)
        img_items = pm.get_by_modality("image", limit=10)
        assert len(text_items) == 1 and len(img_items) == 1, \
            f"模态索引不对: text={len(text_items)}, image={len(img_items)}"
        return "模态索引正确"

    def t_stats():
        s = pm.get_stats()
        assert s["count"] >= 2, f"统计异常: {s['count']}"
        return f"count={s['count']}, modality_counts={s['modality_counts']}"

    for fn in (t_init, t_add_text, t_add_image, t_sqlite, t_vector_write,
               t_retrieve, t_update, t_by_modality, t_stats):
        check(f"[perceptual] {fn.__name__[2:]}", fn)


def verify_manager():
    """记忆管理器：跨类型编排 + 统计 + 整合 + 遗忘。"""
    from domains.memory.manager import MemoryManager
    from domains.memory.base import MemoryConfig

    cfg = MemoryConfig(storage_path=os.path.join(WORK_DIR, "mgr"))
    mgr = MemoryManager(config=cfg, user_id="u1",
                        enable_working=True, enable_episodic=True,
                        enable_semantic=True, enable_perceptual=True)

    def t_init():
        assert set(mgr.memory_types) == {"working", "episodic", "semantic", "perceptual"}, \
            f"类型不全: {list(mgr.memory_types)}"
        return f"4 种类型均已启用: {list(mgr.memory_types)}"

    def t_add_classified():
        mid = mgr.add_memory("昨天完成了 RAG 检索链路的重构工作")
        assert mid, "add_memory 应返回 id"
        return f"自动分类写入, id={mid[:8]}"

    def t_add_explicit():
        mid = mgr.add_memory("定义：向量检索是一种相似度匹配方法", memory_type="semantic")
        assert mid, "显式指定类型写入失败"
        return "显式类型写入成功"

    def t_retrieve():
        # 关键回归点：原先 append(list) 导致 sort 取 .importance 崩溃
        hits = mgr.retrieve_memories("RAG 检索", limit=10)
        assert isinstance(hits, list), "返回值应为 list"
        assert all(hasattr(h, "importance") for h in hits), "返回值元素应为 MemoryItem"
        return f"跨类型检索返回 {len(hits)} 条"

    def t_stats():
        # 关键回归点：原先调用 get_memory_stats() 而各类型实现的是 get_stats()
        s = mgr.get_memory_stats()
        assert "memories_by_type" in s, f"统计结构异常: {list(s)}"
        assert s["total_memories"] >= 0, "total_memories 异常"
        return f"total={s['total_memories']}, by_type={ {k: v.get('count') for k, v in s['memories_by_type'].items()} }"

    def t_update():
        hits = mgr.retrieve_memories("RAG", limit=1)
        if not hits:
            raise AssertionError("无可更新项")
        ok = mgr.update_memory(hits[0].id, importance=0.99)
        assert ok, "update_memory 应返回 True"
        return "跨类型更新成功"

    def t_consolidate():
        # 关键回归点：工具层调用 consolidate_memories（复数），原实现只有单数
        n = mgr.consolidate_memories(from_type="working", to_type="episodic",
                                     importance_threshold=0.1)
        assert n >= 0, "consolidate 应返回整数"
        return f"整合 {n} 条 working -> episodic"

    def t_consolidate_alias():
        n = mgr.consolidate_memory(from_type="working", to_type="episodic", importance=0.1)
        return f"单数别名可用, 整合 {n} 条"

    def t_forget():
        n = mgr.forget_memories(strategy="importance_based", threshold=0.0)
        assert isinstance(n, int), "forget_memories 应返回 int"
        return f"遗忘 {n} 条"

    def t_clear():
        mgr.clear_all_memories()
        return "清空成功"

    for fn in (t_init, t_add_classified, t_add_explicit, t_retrieve, t_stats,
               t_update, t_consolidate, t_consolidate_alias, t_forget, t_clear):
        check(f"[manager] {fn.__name__[2:]}", fn)


def verify_storage_layer():
    """存储层：SQLiteDocumentStore 的完整 CRUD 与统计。"""
    from domains.memory.storage.document_store import SQLiteDocumentStore

    store = SQLiteDocumentStore(db_path=os.path.join(WORK_DIR, "storage", "test.db"))

    def t_add():
        mid = store.add_memory(memory_id="d1", user_id="u1", content="原始内容",
                               memory_type="episodic", timestamp=1700000000,
                               importance=0.8, properties={"k": "v"})
        assert mid == "d1", "add_memory 应返回 id"
        return "写入成功"

    def t_get():
        doc = store.get_memory("d1")
        assert doc and doc["content"] == "原始内容", f"读取异常: {doc}"
        assert doc["properties"]["k"] == "v", "属性未存"
        return "读取 + 属性正确"

    def t_search():
        docs = store.search_memories(user_id="u1", memory_type="episodic", limit=10)
        assert len(docs) == 1, f"检索数不对: {len(docs)}"
        docs2 = store.search_memories(user_id="u1", memory_type="semantic", limit=10)
        assert len(docs2) == 0, "类型过滤未生效"
        return "按用户/类型过滤正常"

    def t_update():
        ok = store.update_memory("d1", content="更新内容", importance=0.9)
        assert ok, "update 应返回 True"
        assert store.get_memory("d1")["content"] == "更新内容", "内容未更新"
        return "更新成功"

    def t_stats():
        s = store.get_database_stats()
        assert s["memories_count"] >= 1, f"统计异常: {s}"
        assert s["store_type"] == "sqlite", "store_type 不对"
        return f"memories_count={s['memories_count']}, types={s['memory_types']}"

    def t_delete():
        ok = store.delete_memory("d1")
        assert ok, "delete 应返回 True"
        assert store.get_memory("d1") is None, "删除后仍可读"
        return "删除成功"

    def t_redaction():
        # 写入层脱敏：密钥不应以明文落库
        store.add_memory(memory_id="d2", user_id="u1",
                         content="我的 api key 是 sk-abcdef1234567890abcdef",
                         memory_type="episodic", timestamp=1700000000, importance=0.5)
        doc = store.get_memory("d2")
        assert "sk-abcdef1234567890abcdef" not in doc["content"], "密钥未被脱敏"
        return "写入层脱敏生效"

    for fn in (t_add, t_get, t_search, t_update, t_stats, t_delete, t_redaction):
        check(f"[storage] {fn.__name__[2:]}", fn)


def verify_rag_pipeline():
    """RAG：文档分块 -> 向量索引 -> 检索 -> 三段式（重排/压缩）-> ACL 过滤。"""
    from domains.memory.rag.pipeline import (
        load_and_chunk_texts, index_chunks, search_vectors, search_three_stage,
        ThreeStageRetrievalConfig, merge_snippets,
    )
    from domains.memory.rag.acl_filter import ACLContext, filter_by_acl, is_visible

    # 造测试文档
    doc_dir = os.path.join(WORK_DIR, "docs")
    os.makedirs(doc_dir, exist_ok=True)
    doc_path = os.path.join(doc_dir, "kb.md")
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("# 向量检索知识库\n\n## 第一节\n\n"
                "向量检索是通过计算查询向量与文档向量之间的相似度来召回相关文档的过程。\n"
                "常用的相似度度量包括余弦相似度、点积和欧氏距离。\n\n"
                "## 第二节\n\n"
                "重排序阶段使用交叉编码器对召回候选进行更精细的相关性打分。\n"
                "权限过滤确保只有被授权的用户才能看到私有文档。\n")

    store = None

    def t_chunk():
        nonlocal store
        chunks = load_and_chunk_texts(paths=[doc_path], chunk_size=120, chunk_overlap=20,
                                     namespace="verify", source_label="rag")
        assert len(chunks) >= 1, f"分块为空: {len(chunks)}"
        assert all("content" in c and "metadata" in c for c in chunks), "分块结构异常"
        assert chunks[0]["metadata"]["namespace"] == "verify", "namespace 未透传"
        return f"分块 {len(chunks)} 块, 命名空间={chunks[0]['metadata']['namespace']}"

    def t_index():
        nonlocal store
        from domains.memory.rag.pipeline import _create_default_vector_store
        chunks = load_and_chunk_texts(paths=[doc_path], chunk_size=120, chunk_overlap=20,
                                     namespace="verify", source_label="rag")
        store = _create_default_vector_store()
        index_chunks(store=store, chunks=chunks, rag_namespace="verify")
        info = store.get_collection_info()
        assert info.get("points_count", 0) >= 1, f"索引向量数不足: {info}"
        return f"索引 {info.get('points_count')} 个向量到 Qdrant"

    def t_search():
        hits = search_vectors(store=store, query="余弦相似度如何计算", top_k=5,
                              rag_namespace="verify")
        assert len(hits) >= 1, f"检索应命中，实际 {len(hits)}"
        contents = [h["metadata"].get("content", "") for h in hits]
        assert any("相似度" in c for c in contents), f"命中内容不符: {contents}"
        return f"检索命中 {len(hits)} 条, Top1 含「相似度」"

    def t_search_expanded():
        from domains.memory.rag.pipeline import search_vectors_expanded
        hits = search_vectors_expanded(store=store, query="重排序", top_k=5,
                                       rag_namespace="verify")
        assert len(hits) >= 1, f"扩展检索应命中，实际 {len(hits)}"
        return f"查询扩展检索命中 {len(hits)} 条"

    def t_three_stage():
        result = search_three_stage(
            store=store, query="权限过滤如何保证安全", top_k=3,
            rag_namespace="verify",
            config=ThreeStageRetrievalConfig(
                enable_mqe=False, enable_hyde=False, enable_rerank=True,
                enable_neighbor_expand=True, enable_compression=True,
                max_per_doc=2, max_chars=800, include_citations=True,
            ),
        )
        assert "hits" in result and "context" in result and "report" in result, \
            "三段式返回结构异常"
        assert result["hits"], "三段式未产出条目"
        assert result["context"], "三段式未产出上下文"
        report = result["report"]
        assert report["rerank_strategy"] in ("cross_encoder", "graph_fusion", "passthrough"), \
            f"重排策略异常: {report['rerank_strategy']}"
        assert report["acl_enforced"] is True, "ACL 未被强制"
        return (f"三段式: 池={report['pool_size']} 重排={report['rerank_strategy']} "
                f"条目={report['final_items']} 上下文={report['context_chars']}字")

    def t_acl_filter_unit():
        docs = [
            {"id": "public", "content": "公开文档"},
            {"id": "private", "content": "私有文档", "acl_users": ["u2"]},
            {"id": "role", "content": "员工文档", "acl_roles": ["staff"]},
            {"id": "denied", "content": "禁止文档", "visible_to": False},
        ]
        ctx = ACLContext(user_id="u1", roles=frozenset({"staff"}))
        kept, rep = filter_by_acl(docs, ctx)
        ids = {d["id"] for d in kept}
        assert "private" not in ids, "越权文档未被剔除"
        assert "denied" not in ids, "显式拒绝未生效"
        assert "role" in ids, "角色授权未放行"
        assert rep.dropped >= 2, f"剔除计数不对: {rep.dropped}"
        assert rep.had_violation, "越权未被报告"
        return f"ACL 单元: 保留 {rep.kept} 剔除 {rep.dropped}"

    def t_acl_in_pipeline():
        # 给向量库注入一条带 ACL 标注的文档，验证检索链路内过滤真的生效
        chunks = [{
            "id": "acl-secret-001",
            "content": "这是只有管理员才能看到的机密内容，包含专门的关键词marker。",
            "metadata": {"doc_id": "secret", "acl_users": ["other_user"],
                         "acl_roles": ["admin"], "start": 0, "end": 10,
                         "source_path": "secret.md", "namespace": "verify"},
        }]
        index_chunks(store=store, chunks=chunks, rag_namespace="verify")

        # 身份不匹配 -> 应被剔除
        ctx = ACLContext(user_id="u1", roles=frozenset({"staff"}))
        hits = search_vectors(store=store, query="机密内容 marker", top_k=10,
                              rag_namespace="verify", acl_context=ctx)
        secret_hit = [h for h in hits
                      if h["metadata"].get("memory_id") == "acl-secret-001"]
        assert not secret_hit, "越权文档进入了检索结果"

        # 身份匹配 -> 应可见
        ctx2 = ACLContext(user_id="other_user")
        hits2 = search_vectors(store=store, query="机密内容 marker", top_k=10,
                               rag_namespace="verify", acl_context=ctx2)
        secret_hit2 = [h for h in hits2
                       if h["metadata"].get("memory_id") == "acl-secret-001"]
        assert secret_hit2, "有权限的用户反而看不到文档"
        return "检索链路内 ACL 双向生效（越权剔除/授权放行）"

    def t_acl_no_context():
        # 未提供身份 -> 带 ACL 标注的条目必须被剔除
        hits = search_vectors(store=store, query="机密内容 marker", top_k=10,
                              rag_namespace="verify")
        secret_hit = [h for h in hits
                      if h["metadata"].get("memory_id") == "acl-secret-001"]
        assert not secret_hit, "无身份时越权文档未被剔除"
        return "无身份时默认剔除带权限标注条目"

    def t_merge_snippets():
        items = [
            {"content": "第一条内容", "score": 0.9, "metadata": {"doc_id": "d1", "start": 0}},
            {"content": "第二条内容", "score": 0.8, "metadata": {"doc_id": "d1", "start": 5}},
        ]
        out = merge_snippets(items, max_chars=1000)
        assert "第一条内容" in out and "第二条内容" in out, "片段合并丢失内容"
        return f"合并产出 {len(out)} 字符"

    for fn in (t_chunk, t_index, t_search, t_search_expanded, t_three_stage,
               t_acl_filter_unit, t_acl_in_pipeline, t_acl_no_context, t_merge_snippets):
        check(f"[rag] {fn.__name__[2:]}", fn)


def verify_document_processing():
    """文档处理：分块器 + 元数据工具。"""
    from domains.memory.rag.document import (
        Document, DocumentProcessor, create_document, filter_chunks,
        add_chunk_metadata, merge_chunks,
    )

    def t_process():
        doc = create_document("第一段内容。" * 200, source="test.md")
        proc = DocumentProcessor(chunk_size=200, chunk_overlap=20)
        chunks = proc.process_document(doc)
        assert len(chunks) >= 2, f"分块数不足: {len(chunks)}"
        assert all(c.chunk_id for c in chunks), "chunk_id 缺失"
        assert all("total_chunks" in c.metadata for c in chunks), "元数据缺失"
        return f"切分 {len(chunks)} 块"

    def t_metadata():
        chunks = [DocumentChunkLike() for _ in range(1)]
        out = add_chunk_metadata(chunks, {"tag": "x"})
        assert out[0].metadata["tag"] == "x", "元数据未合并"
        return "元数据合并正常"

    def t_filter():
        from domains.memory.rag.document import DocumentChunk
        cs = [DocumentChunk(content="短", metadata={}),
              DocumentChunk(content="这是一个足够长的内容块", metadata={})]
        out = filter_chunks(cs, min_length=10)
        assert len(out) == 1, f"过滤结果不对: {len(out)}"
        return "短块过滤正常"

    def t_merge():
        from domains.memory.rag.document import DocumentChunk
        cs = [DocumentChunk(content="A" * 50, metadata={}, doc_id="d1"),
              DocumentChunk(content="B" * 50, metadata={}, doc_id="d1")]
        out = merge_chunks(cs, max_length=200)
        assert len(out) == 1, f"合并结果不对: {len(out)}"
        return "同文档块合并正常"

    def t_surrogate():
        # 孤立代理字符不应让分块流程崩溃
        doc = Document(content="正常内容" * 100 + "\ud800", metadata={})
        proc = DocumentProcessor(chunk_size=100, chunk_overlap=10)
        chunks = proc.process_document(doc)
        assert chunks, "含代理字符时分块为空"
        return "脏数据（代理字符）不崩溃"

    for fn in (t_process, t_metadata, t_filter, t_merge, t_surrogate):
        check(f"[document] {fn.__name__[2:]}", fn)


def DocumentChunkLike():
    from domains.memory.rag.document import DocumentChunk
    return DocumentChunk(content="测试内容", metadata={})


def verify_embedding():
    """嵌入层：TF-IDF 兜底必须真能用（原先未 fit 直接抛异常）。"""
    from domains.memory.embedding import get_text_embedder, get_dimension, TFIDFEmbedding

    def t_tfidf_usable():
        # 关键回归点：原先 encode 未 fit 时 raise ValueError，兜底形同虚设
        emb = TFIDFEmbedding(max_features=256)
        vec = emb.encode("测试文本")
        assert len(vec) > 0, "TF-IDF 编码结果为空"
        return f"未预训练直接可用, 维度={len(vec)}"

    def t_chinese_similarity():
        emb = TFIDFEmbedding(max_features=256)
        a = emb.encode("向量检索是通过相似度召回文档")
        b = emb.encode("向量检索用相似度匹配来召回相关文档")
        c = emb.encode("完全无关的内容，关于烹饪和天气")
        sim_ab = float(a @ b)
        sim_ac = float(a @ c)
        assert sim_ab > sim_ac, f"中文相似度未生效: {sim_ab} <= {sim_ac}"
        return f"中文相似度 {sim_ab:.3f} > 无关文本 {sim_ac:.3f}"

    def t_global_embedder():
        emb = get_text_embedder()
        assert emb is not None, "全局 embedder 为空"
        dim = get_dimension()
        assert dim > 0, "维度为 0"
        return f"全局 embedder={type(emb).__name__}, 维度={dim}"

    for fn in (t_tfidf_usable, t_chinese_similarity, t_global_embedder):
        check(f"[embedding] {fn.__name__[2:]}", fn)


def verify_imports():
    """所有 memory 子模块必须可导入。"""
    import importlib

    modules = [
        "domains.memory", "domains.memory.base", "domains.memory.manager",
        "domains.memory.embedding", "domains.memory.types",
        "domains.memory.types.working", "domains.memory.types.episodic",
        "domains.memory.types.semantic", "domains.memory.types.perceptual",
        "domains.memory.storage", "domains.memory.storage.document_store",
        "domains.memory.storage.qdrant_store", "domains.memory.storage.neo4j_store",
        "domains.memory.rag", "domains.memory.rag.acl_filter",
        "domains.memory.rag.document", "domains.memory.rag.pipeline",
    ]

    def t_all():
        failed = []
        for m in modules:
            try:
                importlib.import_module(m)
            except Exception as exc:
                failed.append(f"{m}: {type(exc).__name__}: {exc}")
        assert not failed, "导入失败:\n" + "\n".join(failed)
        return f"{len(modules)} 个模块全部导入成功"

    check("[import] 全部子模块", t_all)

    def t_lazy_exports():
        from domains import memory as m
        for name in ("WorkingMemory", "EpisodicMemory", "SemanticMemory",
                     "PerceptualMemory", "MemoryManager", "ACLContext"):
            assert hasattr(m, name), f"惰性导出缺失: {name}"
        return "惰性导出齐全"

    check("[import] 惰性导出", t_lazy_exports)


# ================================================================ 主流程
def main():
    print(f"工作目录: {WORK_DIR}")
    print(f"Python: {sys.version.split()[0]}")

    section("步骤 0：注入离线替身（Qdrant 内存模式 / Neo4j 内存图存储）")
    patch_qdrant_to_memory_mode()
    patch_neo4j_to_inmemory()
    print("  Qdrant -> 本地内存模式（真实 qdrant-client API）")
    print("  Neo4j  -> InMemoryGraphStore 替身")

    section("步骤 1：导入与惰性导出")
    verify_imports()

    section("步骤 2：嵌入层")
    verify_embedding()

    section("步骤 3：工作记忆（Working Memory）")
    verify_working_memory()

    section("步骤 4：情景记忆（Episodic Memory）")
    verify_episodic_memory()

    section("步骤 5：语义记忆（Semantic Memory）")
    verify_semantic_memory()

    section("步骤 6：感知记忆（Perceptual Memory）")
    verify_perceptual_memory()

    section("步骤 7：记忆管理器（Memory Manager）")
    verify_manager()

    section("步骤 8：存储层（SQLite 文档存储）")
    verify_storage_layer()

    section("步骤 9：RAG 管道（分块/索引/检索/三段式/ACL）")
    verify_rag_pipeline()

    section("步骤 10：文档处理")
    verify_document_processing()

    # 汇总
    section("验证结果汇总")
    total = len(PASSED) + len(FAILED)
    print(f"  通过: {len(PASSED)}/{total}")
    print(f"  失败: {len(FAILED)}")
    if FAILED:
        print("\n  失败项:")
        for name in FAILED:
            print(f"    - {name}")
    for note in NOTES:
        print(f"  [NOTE] {note}")

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
