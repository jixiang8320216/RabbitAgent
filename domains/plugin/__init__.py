"""domains.plugin 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentSlot": "domains.plugin.domain.contribution",
    "AgentSlotDeclaration": "domains.plugin.domain.contribution",
    "ContributionPolicy": "domains.plugin.domain.contribution",
    "ContributionType": "domains.plugin.domain.contribution",
    "ExecutionGuardConfig": "domains.plugin.infrastructure.execution_guard",
    "ExecutionPolicy": "domains.plugin.infrastructure.execution_guard",
    "Hooks": "domains.plugin.domain.contribution",
    "ImutablePluginModel": "domains.plugin.domain.contribution",
    "ModeBuilder": "domains.plugin.domain.contribution",
    "ModeDeclaration": "domains.plugin.domain.contribution",
    "ModeDefinition": "domains.plugin.domain.contribution",
    "PackageComponents": "domains.plugin.domain.contribution",
    "Plugin": "domains.plugin.domain.contribution",
    "PluginEntryPoint": "domains.plugin.domain.contribution",
    "PluginExecutionGuard": "domains.plugin.infrastructure.execution_guard",
    "PluginInput": "domains.plugin.domain.contribution",
    "PluginManager": "domains.plugin.application.plugin_manager",
    "PluginMeta": "domains.plugin.domain.contribution",
    "PluginSourceLoader": "domains.plugin.infrastructure.plugin_loading",
    "PluginToolconflictError": "domains.plugin.application.plugin_manager",
    "SourceMetadata": "domains.plugin.infrastructure.plugin_loading",
    "TaskCompleteInput": "domains.plugin.domain.contribution",
    "TaskCompleteOutput": "domains.plugin.domain.contribution",
    "TaskCompleteStatus": "domains.plugin.domain.contribution",
    "ToolDeltaEmitter": "domains.plugin.domain.contribution",
    "ToolGroupDefinition": "domains.plugin.domain.contribution",
    "get_current_slot": "domains.plugin.application.request_context",
    "get_plugin_manager": "domains.plugin.application.request_context",
    "get_request_disabled_plugins": "domains.plugin.application.request_context",
    "set_current_slot": "domains.plugin.application.request_context",
    "set_plugin_manager": "domains.plugin.application.request_context",
    "set_plugin_manager_getter": "domains.plugin.application.request_context",
    "set_request_disbled_plugins": "domains.plugin.application.request_context",}


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
