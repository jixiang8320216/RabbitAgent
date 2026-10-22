"""domains.context 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "ACPRuntimeBuild": "domains.context.runtime_builder",
    "ACPRuntimeDescriptor": "domains.context.runtime_builder",
    "AppConfig": "domains.context.runtime_builder",
    "ConfigData": "domains.context.runtime_builder",
    "ConfigMapping": "domains.context.runtime_builder",
    "DEFAULT_MODES": "domains.context.runtime_builder",
    "HermesGatewayService": "domains.context.runtime_builder",
    "JSON_CONFIG_ADAPTER": "domains.context.runtime_builder",
    "ContextOptimizer": "domains.context.application.context_optimizer",
    "DEFAULT_COMPRESSION_THRESHOLD": "domains.context.application.history_compression",
    "HistoryCompressionRegistry": "domains.context.application.history_compression",
    "HistoryCompressionService": "domains.context.application.history_compression",
    "LLMConfig": "domains.context.runtime_builder",
    "LayeredExpertConfigurationSource": "domains.context.runtime_builder",
    "LocalHookConfigurationStore": "domains.context.runtime_builder",
    "LocalHookFileWatcher": "domains.context.runtime_builder",
    "LocalHookResourceLoader": "domains.context.runtime_builder",
    "LocalInstructionResourceLoader": "domains.context.runtime_builder",
    "LocalSkillDiscoveryService": "domains.context.runtime_builder",
    "MCPServerConfig": "domains.context.runtime_builder",
    "MCPServerResource": "domains.context.runtime_builder",
    "MemoryRuntimeSettings": "domains.context.runtime_builder",
    "RuntimeAppConfigOptions": "domains.context.runtime_builder",
    "RuntimeConfigSnapshot": "domains.context.runtime_builder",
    "RuntimeContext": "domains.context.runtime_builder",
    "RuntimeLLMProfile": "domains.context.runtime_builder",
    "RuntimeModelProfileOverride": "domains.context.runtime_builder",
    "SessionMode": "domains.context.runtime_builder",
    "SystemResources": "domains.context.runtime_builder",
    "bootstrap_context": "domains.context.runtime_builder",
    "build_runtime_context": "domains.context.runtime_builder",
    "build_runtime": "domains.context.runtime_builder",
    "build_runtime_descriptor": "domains.context.runtime_builder",
    "load_runtime_merged_config": "domains.context.runtime_builder",}


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
