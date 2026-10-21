"""interfaces.commands.implementation 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentsCommand": "interfaces.commands.implementation.info_commands",
    "BTWCommand": "interfaces.commands.implementation.btw_cmd",
    "ClearCommand": "interfaces.commands.implementation.core_commands",
    "CommandProtocol": "interfaces.commands.implementation.btw_cmd",
    "CommandResult": "interfaces.commands.implementation.btw_cmd",
    "ConfigCommand": "interfaces.commands.implementation.config_cmd",
    "ConfigDecryptCommand": "interfaces.commands.implementation.encryption_commands",
    "ConfigEncryptCommand": "interfaces.commands.implementation.encryption_commands",
    "ExitCommand": "interfaces.commands.implementation.exit_cmd",
    "HelpCommand": "interfaces.commands.implementation.help_cmd",
    "HookCommand": "interfaces.commands.implementation.hook_command",
    "InterruptCommand": "interfaces.commands.implementation.core_commands",
    "LSPCommand": "interfaces.commands.implementation.lsp_cmd",
    "MCPCommand": "interfaces.commands.implementation.mcp_cmd",
    "MemoryCommand": "interfaces.commands.implementation.memory_commands",
    "ModeCommand": "interfaces.commands.implementation.mode_cmd",
    "ModelCommand": "interfaces.commands.implementation.config_cmd",
    "MultilineCommand": "interfaces.commands.implementation.core_commands",
    "PlanCommand": "interfaces.commands.implementation.info_commands",
    "ResetCommand": "interfaces.commands.implementation.reset_cmd",
    "SessionCommand": "interfaces.commands.implementation.session_commands",
    "SettingsCommand": "interfaces.commands.implementation.config_commands",
    "ShellCommand": "interfaces.commands.implementation.shell_cmd",
    "SkillsCommand": "interfaces.commands.implementation.info_commands",
    "StatusCommand": "interfaces.commands.implementation.status_cmd",
    "ThemeCommand": "interfaces.commands.implementation.config_cmd",
    "TokensCommand": "interfaces.commands.implementation.core_commands",
    "WebCommand": "interfaces.commands.implementation.core_commands",}


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
