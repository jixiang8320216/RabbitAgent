"""interfaces.tui 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "ApplicationDisplay": "interfaces.tui.display.application.application_display",
    "BaseToolHandler": "interfaces.tui.display.tools.base_tool_handler",
    "COMMAND_SUBCOMMANDS": "interfaces.tui.user_input.completer",
    "CliCompleter": "interfaces.tui.user_input.completer",
    "DefaultToolHandler": "interfaces.tui.display.tools.default_tool_handler",
    "DynamicSpinner": "interfaces.tui.widgets.dinamic_spinner",
    "ERROR_COLOR": "interfaces.tui.display.cli_theme",
    "EditFileHandler": "interfaces.tui.display.tools.edit_file_handler",
    "EventProtocol": "interfaces.tui.tui_observer",
    "ExecuteCommandHandler": "interfaces.tui.display.tools.execute_command_handler",
    "GenerateResponseHandler": "interfaces.tui.display.tools.generate_response_handler",
    "GlobToolHandler": "interfaces.tui.display.tools.glob_tool_handler",
    "GrepToolHandler": "interfaces.tui.display.tools.grep_tool_handler",
    "InteractionDisplay": "interfaces.tui.display.application.interaction_display",
    "KnowledgeRetrieveHandler": "interfaces.tui.display.tools.knowledge_retrieve_handler",
    "PATH_TERMINATORS_PATTERN": "interfaces.tui.user_input.processor",
    "ReActAgent": "interfaces.tui.display.skills.skill_status_handler",
    "RabbitObserver": "interfaces.tui.tui_observer",
    "ReplayHandler": "interfaces.tui.display.relay.relay_handler",
    "RetryState": "interfaces.tui.tui_observer",
    "SHELL_SPECIAL_CHARS_PATTERN": "interfaces.tui.user_input.processor",
    "SUCCESS_COLOR": "interfaces.tui.display.cli_theme",
    "SkillStatusHandler": "interfaces.tui.display.skills.skill_status_handler",
    "StructuredToolResult": "interfaces.tui.tui_observer",
    "TUIApp": "interfaces.tui.widgets.tui_app",
    "TUIConsoleAdapter": "interfaces.tui.tui_observer",
    "TUIObserver": "interfaces.tui.tui_observer",
    "ThinkingHandler": "interfaces.tui.display.thinking.thinking_handler",
    "UpdateFileToolHandler": "interfaces.tui.display.tools.update_file_tool_handler",
    "UserInputArea": "interfaces.tui.widgets.user_input_area",
    "ViewTextFileHandler": "interfaces.tui.display.tools.view_text_file_handler",
    "WriteFileHandler": "interfaces.tui.display.tools.write_file_handler",
    "format_rabbit_footer": "interfaces.tui.display.render_utils",
    "process_user_input": "interfaces.tui.user_input.processor",
    "split_think_content": "interfaces.tui.display.render_utils",}


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
