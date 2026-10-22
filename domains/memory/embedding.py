"""统一嵌入模块(实现+提供器)

说明（中文）：
- 提供统一的文本嵌入接口与多实现：本地Transformer、DashScope（通义千问）、TF-IDF兜底。
- 暴露 get_text_embedder()/get_dimension()/refresh_embedder() 供各记忆类型统一使用。
- 通过环境变量优先级：dashscope > local > tfidf。

环境变量：
- EMBED_MODEL_TYPE: "dashscope" | "local" | "tfidf"（默认 dashscope）
- EMBED_MODEL_NAME: 模型名称（dashscope默认 text-embedding-v3；local默认 sentence-transformers/all-MiniLM-L6-v2）
- EMBED_API_KEY: Embedding API Key（统一命名）
- EMBED_BASE_URL: Embedding Base URL（统一命名，可选）
"""

from typing import List, Union, Optional
import threading
import os
import numpy as np


# ==============
# 抽象与实现
# ==============

class EmbeddingModel:
    """ 嵌入模型基类(最小接口)"""
    def encode(self, texts: Union[str, List[str]]):
        raise NotImplementedError

    @property
    def dimension(self) -> int:
        raise NotImplementedError

class LocalTransformerEmbedding(EmbeddingModel):
     """本地Transformer嵌入（优先 sentence-transformers，缺失回退 transformers+torch）"""
     def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
         self.model_name = model_name
         self._backend = None
         self._st_model =None
         self._hf_tokenizer = None
         self._hf_model = None
         self._dimension = None
         self._load_backend()

     def _load_backend(self):
         # 优先 sentence-transformers
         try:
             from sentence_transformers import SentenceTransformer
             self._st_model = SentenceTransformer(self.model_name)
             test_vec = self._st_model.encode("test_text")
             self._dimension = len(test_vec)
             self._backend = "st"
             return
         except Exception:
             self._st_model = None

         # 回退 transformers
         try:
             from transformers import AutoTokenizer, AutoModel
             import torch
             self._hf_tokenizer = AutoTokenizer.from_pretrained(self.model_name)
             self._hf_model = AutoModel.from_pretrained(self.model_name)
             with torch.no_grad():
                 inputs = self._hf_tokenizer("test_text", return_tensors="pt", padding=True, truncation=True)
                 outputs = self._hf_model(**inputs)
                 test_embedding = outputs.last_hidden_state.mean(dim=1)
                 self._dimension = int(test_embedding.shape[1])
             self._backend = "hf"
             return
         except Exception:
             self._hf_tokenizer = None
             self._hf_model = None

         raise ImportError("未找到可用的本地嵌入后端，请安装 sentence-transformers 或 transformers+torch")


     def encode(self, texts: Union[str, List[str]]):
        if isinstance(texts, str):
            inputs = [texts]
            single = True
        else:
            inputs = list(texts)
            single = False

        if self._backend == "st":
            vecs = self._st_model.encode(inputs)
            if hasattr(vecs, "tolist"):
                vecs = [v for v in vecs]
        else:
            import torch
            tokenized = self._hf_tokenizer(inputs, return_tensors="pt", padding=True, truncation=True, max_length=512)
            with torch.no_grad():
                outputs = self._hf_model(**tokenized)
                embeddings = outputs.last_hidden_state.mean(dim=1).cpu().numpy()
            vecs = [v for v in embeddings]

        if single:
            return vecs[0]
        return vecs

     @property
     def dimension(self) -> int:
         return int(self._dimension or 0)

class TFIDFEmbedding(EmbeddingModel):
    """TF-IDF 简易兜底（在无深度模型时保证可用）。

    三个关键修正（原先这个兜底在离线环境实际是不可用的）：

    1. **未 fit 不再抛异常**。原 ``encode`` 在 ``_is_fitted=False`` 时直接
       ``raise ValueError``，而工厂链 ``dashscope -> local -> tfidf`` 从不会
       调用 ``fit()``，于是「本地无模型 + 无 API Key」时唯一剩下的兜底
       必然失败。
    2. **分词支持中文**。``stop_words='english'`` 对中文没有意义，且默认
       词级切分会把整段中文当成一个 token，导致相似度恒为 0。
       改用 ``analyzer='char_wb'`` 的字符 n-gram，对中英文都可用。
    3. **维度固定**（关键）。原先用 ``TfidfVectorizer``，实际维度等于
       **语料里出现过的特征数**——一段短文本 fit 出来可能只有 10 维，
       而 ``dimension`` 属性此时仍报 ``max_features``（1000）。于是
       「建 Qdrant 集合」按 1000 维建、「实际写入的向量」只有 26 维，
       全部被 ``add_vectors`` 的维度校验拒绝——**向量库静默写不进任何东西**。
       现在改用 ``HashingVectorizer``：无状态、维度恒为 ``max_features``，
       跨语料稳定，这才符合「向量维度必须可预期」的要求。
    """

    def __init__(self, max_features: int = 1000):
        self.max_features = max_features
        self._vectorizer = None
        self._is_fitted = True  # HashingVectorizer 无需训练
        self._dimension = max_features
        self._init_vectorizer()

    def _init_vectorizer(self):
        try:
            from sklearn.feature_extraction.text import HashingVectorizer
            # HashingVectorizer：无状态哈希编码，输出维度恒为 n_features，
            # 不需要 fit，也不会因语料变化而改变维度。
            # analyzer='char_wb'：字符 n-gram，中英文通用。
            self._vectorizer = HashingVectorizer(
                n_features=self.max_features,
                analyzer="char_wb",
                ngram_range=(1, 2),
                lowercase=True,
                norm="l2",
                alternate_sign=False,
            )
        except ImportError:
            raise ImportError("请安装 scikit-learn: pip install scikit-learn")

    def fit(self, texts: List[str]):
        """兼容接口。HashingVectorizer 无需训练，此处仅保持调用方契约。"""
        self._is_fitted = True
        return self

    def encode(self, texts: Union[str, List[str]]):
        if isinstance(texts, str):
            inputs = [texts]
            single = True
        else:
            inputs = list(texts)
            single = False
        tfidf_matrix = self._vectorizer.transform(inputs)
        embeddings = tfidf_matrix.toarray()
        if single:
            return embeddings[0]
        return [e for e in embeddings]

    @property
    def dimension(self) -> int:
        return self._dimension


class DashScopeEmbedding(EmbeddingModel):
    """阿里云 DashScope（通义千问）Embedding / OpenAI兼容REST 模式

    行为：
    - 如提供 base_url，则优先使用 OpenAI 兼容的 REST 接口（POST {base_url}/embeddings）。
    - 否则使用官方 dashscope SDK 的 TextEmbedding.call。
    """

    def __init__(self, model_name: str = "text-embedding-v3", api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self._dimension = None
        # 仅在非REST情况下初始化SDK
        if not self.base_url:
            self._init_client()
        # 探测维度
        test = self.encode("health_check")
        self._dimension = len(test)

    def _init_client(self):
        try:
            if self.api_key:
                # 将统一命名的 API Key 注入到 SDK 期望的位置
                os.environ["DASHSCOPE_API_KEY"] = self.api_key
            import dashscope  # noqa: F401
        except ImportError:
            raise ImportError("请安装 dashscope: pip install dashscope")

    def encode(self, texts: Union[str, List[str]]):
        if isinstance(texts, str):
            inputs = [texts]
            single = True
        else:
            inputs = list(texts)
            single = False

        # REST 模式（OpenAI兼容）
        if self.base_url:
            import requests
            url = self.base_url.rstrip("/") + "/embeddings"
            headers = {
                "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                "Content-Type": "application/json",
            }
            payload = {"model": self.model_name, "input": inputs}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code >= 400:
                raise RuntimeError(f"Embedding REST 调用失败: {resp.status_code} {resp.text}")
            data = resp.json()
            # 期望结构：{"data": [{"embedding": [...]}]}
            items = data.get("data") or []
            vecs = [np.array(item.get("embedding")) for item in items]
            if single:
                return vecs[0]
            return vecs

        # SDK 模式
        from dashscope import TextEmbedding
        rsp = TextEmbedding.call(model=self.model_name, input=inputs)
        embeddings_obj = None
        if isinstance(rsp, dict):
            embeddings_obj = (rsp.get("output") or {}).get("embeddings")
        else:
            embeddings_obj = getattr(getattr(rsp, "output", None), "embeddings", None)
        if not embeddings_obj:
            raise RuntimeError("DashScope 返回为空或格式不匹配")
        vecs = [np.array(item.get("embedding") or item.get("vector")) for item in embeddings_obj]
        if single:
            return vecs[0]
        return vecs

    @property
    def dimension(self) -> int:
        return int(self._dimension or 0)


# ==============
# 工厂与回退
# ==============

def create_embedding_model(model_type: str = "local", **kwargs) -> EmbeddingModel:
    """创建嵌入模型实例

    model_type: "dashscope" | "local" | "tfidf"
    kwargs: model_name, api_key
    """
    if model_type in ("local", "sentence_transformer", "huggingface"):
        return LocalTransformerEmbedding(**kwargs)
    elif model_type == "dashscope":
        return DashScopeEmbedding(**kwargs)
    elif model_type == "tfidf":
        return TFIDFEmbedding(**kwargs)
    else:
        raise ValueError(f"不支持的模型类型: {model_type}")


def create_embedding_model_with_fallback(preferred_type: str = "dashscope", **kwargs) -> EmbeddingModel:
    """带回退的创建：dashscope -> local -> tfidf

    每一档的 kwargs 会被**按该实现实际接受的参数**过滤后再传入。
    原实现把同一份 kwargs 原样传给所有档位，而各实现的构造签名并不一致
    （``TFIDFEmbedding`` 只吃 ``max_features``，``LocalTransformerEmbedding`` /
    ``DashScopeEmbedding`` 只吃 ``model_name``/``api_key``/``base_url``），
    于是「给 tfidf 传 model_name」会抛 TypeError —— TypeError 不属于
    「模型不可用」，却被这里的 ``except Exception`` 一起吞掉，结果
    **连最后一道纯本地兜底也被跳过**，最终抛「所有嵌入模型都不可用」。
    """
    if preferred_type in ("sentence_transformer", "huggingface"):
        preferred_type = "local"
    fallback = ["dashscope", "local", "tfidf"]
    # 将首选放最前
    if preferred_type in fallback:
        fallback.remove(preferred_type)
        fallback.insert(0, preferred_type)
    # 显式指定 tfidf 时不再尝试 dashscope / local：
    # dashscope 需要网络与 API Key，local 需要下载模型权重，
    # 两者在离线环境都会失败并拖慢启动（local 甚至会先尝试联网下载）。
    # 用户显式指定了实现，就应当尊重它。
    if preferred_type == "tfidf":
        fallback = ["tfidf"]

    # 各实现真正接受的构造参数
    accepted = {
        "dashscope": ("model_name", "api_key", "base_url"),
        "local": ("model_name",),
        "tfidf": ("max_features",),
    }

    for t in fallback:
        try:
            filtered = {k: v for k, v in kwargs.items() if k in accepted.get(t, ())}
            return create_embedding_model(t, **filtered)
        except Exception:
            continue
    raise RuntimeError("所有嵌入模型都不可用，请安装依赖或检查配置")


# ==================
# Provider（单例）
# ==================

_lock = threading.RLock()
_embedder: Optional[EmbeddingModel] = None


def _build_embedder() -> EmbeddingModel:
    preferred = os.getenv("EMBED_MODEL_TYPE", "dashscope").strip()
    # 根据提供商选择默认模型
    default_model = "text-embedding-v3" if preferred == "dashscope" else "sentence-transformers/all-MiniLM-L6-v2"
    model_name = os.getenv("EMBED_MODEL_NAME", default_model).strip()
    kwargs = {}
    # 只有需要「模型名」的实现才接收 model_name；
    # tfidf 档位只认 max_features，传入 model_name 会让它构造失败
    # （进而被回退链跳过，见 create_embedding_model_with_fallback 的说明）。
    if model_name and preferred != "tfidf":
        kwargs["model_name"] = model_name
    # 仅使用统一命名
    api_key = os.getenv("EMBED_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key
    base_url = os.getenv("EMBED_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return create_embedding_model_with_fallback(preferred_type=preferred, **kwargs)


def get_text_embedder() -> EmbeddingModel:
    """获取全局共享的文本嵌入实例（线程安全单例）"""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is None:
            _embedder = _build_embedder()
        return _embedder


def get_dimension(default: int = 384) -> int:
    """获取统一向量维度（失败回退默认值）"""
    try:
        return int(getattr(get_text_embedder(), "dimension", default))
    except Exception:
        return int(default)


def refresh_embedder() -> EmbeddingModel:
    """强制重建嵌入实例（可用于动态切换环境变量）"""
    global _embedder
    with _lock:
        _embedder = _build_embedder()
        return _embedder
