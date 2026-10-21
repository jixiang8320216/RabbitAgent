"""domains.expert 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "EXPERT_CONFIG_FILENAME": "domains.expert.infrastructure.expert_repository",
    "EXPERT_LOCK_TIMEOUT_SECONDS": "domains.expert.infrastructure.expert_repository",
    "EXPERT_PROMPT_FILENAME": "domains.expert.infrastructure.expert_repository",
    "EncryptionService": "domains.expert.infrastructure.expert_secret_store",
    "ExpertConfiguration": "domains.expert.infrastructure.expert_source",
    "ExpertConfigurationRepository": "domains.expert.infrastructure.expert_repository",
    "ExpertMcpSecretStore": "domains.expert.infrastructure.expert_secret_store",
    "ExpertScope": "domains.expert.infrastructure.expert_source",
    "FileLockTimeoutError": "domains.expert.infrastructure.expert_repository",
    "FileLockUnsafePathError": "domains.expert.infrastructure.expert_repository",
    "LayeredExpertConfigurationSource": "domains.expert.infrastructure.expert_source",
    "apply_mcp_secret_overrides": "domains.expert.infrastructure.portable_package",
    "load_expert_root": "domains.expert.infrastructure.expert_source",
    "load_resource_lock": "domains.expert.infrastructure.portable_package",}


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
