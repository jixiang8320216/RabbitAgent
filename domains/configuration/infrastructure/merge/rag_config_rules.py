"""RAG 工具的启用开关与运行参数解析。

与记忆开关（``personalization.memory.enabled``，见
``memory_config_rules.resolve_memory_enabled``）保持对称的解析口径。

**默认关闭**的原因：RAG 依赖外部 Qdrant 服务。默认开启会让没有部署
Qdrant 的用户在每次启动时看到一条「RAG 工具初始化失败」的噪音日志，
而这对绝大多数不使用知识库的场景毫无价值。因此 RAG 属于**显式开启**
的能力，需要时用 ``personalization.rag.enabled = true`` 打开。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domains.configuration import ConfigMapping

__all__ = [
    "resolve_rag_enabled",
    "resolve_rag_settings",
]


def resolve_rag_enabled(config: ConfigMapping) -> bool:
    """RAG 工具是否启用。

    解析优先级：

    1. ``personalization.rag.enabled``（布尔）—— 权威开关；
    2. ``plugins.disabled`` 含 ``"rag"`` —— 视为关闭；
    3. 缺省 **False**（RAG 需外部服务，不默认打开）。

    :param config: 分层合并后的配置
    """
    personalization = config.get("personalization")
    if isinstance(personalization, Mapping):
        rag = personalization.get("rag")
        if isinstance(rag, Mapping) and isinstance(rag.get("enabled"), bool):
            return bool(rag["enabled"])
    plugins = config.get("plugins")
    if isinstance(plugins, Mapping):
        disabled = plugins.get("disabled")
        if isinstance(disabled, list) and "rag" in disabled:
            return False
    return False


def resolve_rag_settings(config: ConfigMapping) -> dict[str, Any]:
    """解析 RAG 运行参数。

    支持的键（均在 ``personalization.rag`` 下）：

    - ``enabled``（bool）：是否启用；
    - ``knowledge_base_path``（str）：知识库目录，默认 ``./knowledge_base``；
    - ``collection_name``（str）：Qdrant 集合名，默认 ``rag_knowledge_base``；
    - ``namespace``（str）：RAG 命名空间，默认 ``default``；
    - ``qdrant_url`` / ``qdrant_api_key``（str）：留空则回退到环境变量。

    :param config: 分层合并后的配置
    """
    defaults: dict[str, Any] = {
        "enabled": False,
        "knowledge_base_path": "./knowledge_base",
        "collection_name": "rag_knowledge_base",
        "namespace": "default",
        "qdrant_url": None,
        "qdrant_api_key": None,
    }

    personalization = config.get("personalization")
    if not isinstance(personalization, Mapping):
        return defaults
    rag = personalization.get("rag")
    if not isinstance(rag, Mapping):
        return defaults

    resolved = dict(defaults)
    if isinstance(rag.get("enabled"), bool):
        resolved["enabled"] = bool(rag["enabled"])
    for key in ("knowledge_base_path", "collection_name", "namespace",
                "qdrant_url", "qdrant_api_key"):
        value = rag.get(key)
        if isinstance(value, str) and value.strip():
            resolved[key] = value.strip()
    return resolved
