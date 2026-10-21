"""interfaces.cli 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentExecutorThread": "interfaces.cli.agent_executor",
    "AgentTurnQueue": "interfaces.cli.tui_application",
    "AgentsCommand": "interfaces.cli.tui_application",
    "AppConfig": "interfaces.cli.tui_application",
    "AppV3EventPersistence": "interfaces.cli.tui_application",
    "BTWCommand": "interfaces.cli.tui_application",
    "CLIConfirmationStrategy": "domains.permission.cli_strategy",
    "CLIConfirmationStartegy": "domains.permission.cli_strategy",
    "CheckpointReason": "interfaces.cli.args_parse",
    "ClearCommand": "interfaces.cli.tui_application",
    "CommandRegistry": "interfaces.cli.tui_application",
    "ConfigCommand": "interfaces.cli.tui_application",
    "ConfigDecryptCommand": "interfaces.cli.tui_application",
    "ConfigEncryptCommand": "interfaces.cli.tui_application",
    "ConfirmationRegistry": "interfaces.cli.tui_application",
    "ExitCommand": "interfaces.cli.tui_application",
    "FileCheckpointStore": "interfaces.cli.args_parse",
    "HelpCommand": "interfaces.cli.tui_application",
    "HookCommand": "interfaces.cli.tui_application",
    "InterruptCommand": "interfaces.cli.tui_application",
    "InterruptManager": "interfaces.cli.tui_application",
    "LSPCommand": "interfaces.cli.tui_application",
    "MCPCommand": "interfaces.cli.tui_application",
    "MarkdownCommand": "interfaces.cli.tui_application",
    "MemoryCommand": "interfaces.cli.tui_application",
    "ModeCommand": "interfaces.cli.tui_application",
    "ModelCommand": "interfaces.cli.tui_application",
    "Msg": "interfaces.cli.tui_application",
    "MultilineCommand": "interfaces.cli.tui_application",
    "PlanCommand": "interfaces.cli.tui_application",
    "PromptExpansion": "interfaces.cli.tui_application",
    "RabbitApplication": "interfaces.cli.args_parse",
    "ResetCommand": "interfaces.cli.tui_application",
    "SessionCommand": "interfaces.cli.tui_application",
    "SessionPersistence": "interfaces.cli.args_parse",
    "SettingsCommand": "interfaces.cli.tui_application",
    "ShellCommand": "interfaces.cli.tui_application",
    "SkillsCommand": "interfaces.cli.tui_application",
    "StatusCommand": "interfaces.cli.tui_application",
    "T": "interfaces.cli.tui_application",
    "TUIApplication": "interfaces.cli.tui_application",
    "TUIObserver": "interfaces.cli.tui_application",
    "ThemeCommand": "interfaces.cli.tui_application",
    "TokensCommand": "interfaces.cli.tui_application",
    "WebCommand": "interfaces.cli.tui_application",
    "WorkspaceVersionService": "interfaces.cli.args_parse",
    "build_argument_parser": "interfaces.cli.args_parse",
    "config_config_parser": "interfaces.cli.args_parse",
    "config_hook_parser": "interfaces.cli.args_parse",
    "config_workspace_version": "interfaces.cli.args_parse",
    "execute_config_command": "interfaces.cli.args_parse",
    "execute_hook_action": "interfaces.cli.args_parse",
    "execute_hooks_command": "interfaces.cli.args_parse",
    "render_hook_result": "interfaces.cli.args_parse",
    "run_workspace_version_command": "interfaces.cli.args_parse",}


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
