"""domains.configuration.infrastructure.merge 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "ConfigData": "domains.configuration.infrastructure.merge.config_merger",
    "ConfigMapping": "domains.configuration.infrastructure.merge.config_merger",
    "DEFAULT_MODES": "domains.configuration.infrastructure.merge.config_merger",
    "EncryptionService": "domains.configuration.infrastructure.merge.config_merger",
    "LLMConfig": "domains.configuration.infrastructure.merge.config_merger",
    "LocalMCPDiscoveryService": "domains.configuration.infrastructure.merge.config_merger",
    "ModelInfo": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeLLMProfile": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeLLMProfiled": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeMCPServerConfig": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeMCPTransport": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeModelProfileOverride": "domains.configuration.infrastructure.merge.config_merger",
    "SessionMode": "domains.configuration.infrastructure.merge.config_merger",
    "clean_project_memory_configuration": "domains.configuration.infrastructure.merge.memory_config_rules",
    "decrypt_config_value": "domains.configuration.infrastructure.merge.config_merger",
    "get_default_mode": "domains.configuration.infrastructure.merge.config_merger",
    "load_local_mcp_servers": "domains.configuration.infrastructure.merge.config_merger",
    "load_merged_config": "domains.configuration.infrastructure.merge.config_merger",
    "migrate_user_memory_configuration": "domains.configuration.infrastructure.merge.memory_config_rules",
    "profile_to_llm_config": "domains.configuration.infrastructure.merge.config_merger",
    "profile_to_model_info": "domains.configuration.infrastructure.merge.config_merger",
    "resolve_llm_runtime": "domains.configuration.infrastructure.merge.config_merger",
    "resolve_memory_enabled": "domains.configuration.infrastructure.merge.memory_config_rules",
    "resolve_rag_enabled": "domains.configuration.infrastructure.merge.rag_config_rules",
    "resolve_rag_settings": "domains.configuration.infrastructure.merge.rag_config_rules",}


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
