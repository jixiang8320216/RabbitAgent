"""domains.configuration.infrastructure 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "APP_MODE_CONFIG_KEY": "domains.configuration.infrastructure.adapter.app_mode_resolver",
    "APP_MODE_ENV_VAR": "domains.configuration.infrastructure.adapter.app_mode_resolver",
    "ConfigData": "domains.configuration.infrastructure.merge.config_merger",
    "ConfigManager": "domains.configuration.infrastructure.adapter.config_manager",
    "ConfigMapping": "domains.configuration.infrastructure.merge.config_merger",
    "DEFAULT_APP_MODE": "domains.configuration.infrastructure.adapter.app_mode_resolver",
    "DEFAULT_LLM_CONFIG": "domains.configuration.infrastructure.store.user_config_store",
    "DEFAULT_MODES": "domains.configuration.infrastructure.merge.config_merger",
    "EncryptionService": "domains.configuration.infrastructure.merge.config_merger",
    "FileLockTimeoutError": "domains.configuration.infrastructure.store.project_config_store",
    "FileLockUnsafePathError": "domains.configuration.infrastructure.store.project_config_store",
    "HookMcpClientAdapter": "domains.configuration.infrastructure.adapter.hook_runtime_adapters",
    "HookModelClientAdapter": "domains.configuration.infrastructure.adapter.hook_runtime_adapters",
    "JsonObject": "domains.configuration.infrastructure.store.project_config_store",
    "LLMConfig": "domains.configuration.infrastructure.merge.config_merger",
    "LOGGER_CONFIG": "domains.configuration.infrastructure.logging_bootstrap",
    "LegacyMemoryItem": "domains.configuration.infrastructure.migration.memory_document_migration",
    "LocalMCPDiscoveryService": "domains.configuration.infrastructure.merge.config_merger",
    "MemoryDocumentInitializationResult": "domains.configuration.infrastructure.migration.memory_document_migration",
    "MemoryDocumentStore": "domains.configuration.infrastructure.migration.memory_document_migration",
    "ModelInfo": "domains.configuration.infrastructure.merge.config_merger",
    "PROJECT_CONFIG_LOCK_TIMEOUT_SECONDS": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectConfigBusyError": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectConfigInvalidError": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectConfigUnsafePathError": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectLayout": "domains.configuration.infrastructure.store.project_config_store",
    "RuntimeLLMProfile": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeLLMProfiled": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeMCPServerConfig": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeMCPTransport": "domains.configuration.infrastructure.merge.config_merger",
    "RuntimeModelProfileOverride": "domains.configuration.infrastructure.merge.config_merger",
    "SessionMode": "domains.configuration.infrastructure.merge.config_merger",
    "USER_CONFIG_LOCK_TIMEOUT_SECONDS": "domains.configuration.infrastructure.store.user_config_store",
    "UserConfigBusError": "domains.configuration.infrastructure.store.user_config_store",
    "UserConfigInvalidError": "domains.configuration.infrastructure.store.user_config_store",
    "UserConfigUnsafePathError": "domains.configuration.infrastructure.store.user_config_store",
    "VALID_APP_MODES": "domains.configuration.infrastructure.adapter.app_mode_resolver",
    "clean_project_memory_configuration": "domains.configuration.infrastructure.merge.memory_config_rules",
    "decrypt_config_value": "domains.configuration.infrastructure.merge.config_merger",
    "get_default_mode": "domains.configuration.infrastructure.merge.config_merger",
    "initialize_memory_documents": "domains.configuration.infrastructure.migration.memory_document_migration",
    "load_app_mode": "domains.configuration.infrastructure.adapter.app_mode_resolver",
    "load_local_mcp_servers": "domains.configuration.infrastructure.merge.config_merger",
    "load_merged_config": "domains.configuration.infrastructure.merge.config_merger",
    "load_or_create_user_config": "domains.configuration.infrastructure.store.user_config_store",
    "migrate_user_memory_configuration": "domains.configuration.infrastructure.merge.memory_config_rules",
    "profile_to_llm_config": "domains.configuration.infrastructure.merge.config_merger",
    "profile_to_model_info": "domains.configuration.infrastructure.merge.config_merger",
    "read_project_config": "domains.configuration.infrastructure.store.project_config_store",
    "read_user_config": "domains.configuration.infrastructure.store.user_config_store",
    "resolve_llm_runtime": "domains.configuration.infrastructure.merge.config_merger",
    "resolve_memory_enabled": "domains.configuration.infrastructure.merge.memory_config_rules",
    "resolver_app_mode": "domains.configuration.infrastructure.adapter.app_mode_resolver",
    "update_project_config": "domains.configuration.infrastructure.store.project_config_store",
    "update_user_config": "domains.configuration.infrastructure.store.user_config_store",
    "validate_app_mode": "domains.configuration.infrastructure.adapter.app_mode_resolver",}


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
