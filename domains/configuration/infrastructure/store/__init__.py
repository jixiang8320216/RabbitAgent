"""domains.configuration.infrastructure.store 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "DEFAULT_LLM_CONFIG": "domains.configuration.infrastructure.store.user_config_store",
    "FileLockTimeoutError": "domains.configuration.infrastructure.store.project_config_store",
    "FileLockUnsafePathError": "domains.configuration.infrastructure.store.project_config_store",
    "JsonObject": "domains.configuration.infrastructure.store.project_config_store",
    "PROJECT_CONFIG_LOCK_TIMEOUT_SECONDS": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectConfigBusyError": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectConfigInvalidError": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectConfigUnsafePathError": "domains.configuration.infrastructure.store.project_config_store",
    "ProjectLayout": "domains.configuration.infrastructure.store.project_config_store",
    "USER_CONFIG_LOCK_TIMEOUT_SECONDS": "domains.configuration.infrastructure.store.user_config_store",
    "UserConfigBusError": "domains.configuration.infrastructure.store.user_config_store",
    "UserConfigInvalidError": "domains.configuration.infrastructure.store.user_config_store",
    "UserConfigUnsafePathError": "domains.configuration.infrastructure.store.user_config_store",
    "load_or_create_user_config": "domains.configuration.infrastructure.store.user_config_store",
    "read_project_config": "domains.configuration.infrastructure.store.project_config_store",
    "read_user_config": "domains.configuration.infrastructure.store.user_config_store",
    "update_project_config": "domains.configuration.infrastructure.store.project_config_store",
    "update_user_config": "domains.configuration.infrastructure.store.user_config_store",}


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
