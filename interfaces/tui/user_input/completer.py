from prompt_toolkit.completion import Completer, Completion, CompleteEvent
from prompt_toolkit.document import Document

COMMAND_SUBCOMMANDS={
    "session":{
        "list":"list all saved sessions",
        "create":"create new session",
        "delete":"delete saved session",
        "switch":"switch saved session",
        "copy":"copy saved session",
    },
    "mcp":{
        "list":"list all configured MCP servers",
        "ls":"List all configured MCP servers(alias)",
        "toggle":"Toggle server active state(enable/disable)",
        "connect":"Connect to MCP server.",
        "disconnect":"Disconnect MCP server.",
        "reconnect":"Reconnect to MCP server.",
    },
    "mode":{
        "delegate":"Switch to delegate mode(task delegation).",
        "roleplay":"Switch to roleplay mode(role compliance).",
        "solo":"Switch to solo mode(single agent,no delegation).",
        "groupchat":"Switch to groupchat mode(multi-agent discussion).",
    },
    "play":{
        "reset":"Clear all loaded roles, revert to default.",
    },
    "config":{
        "show":"Show current configuration",
        "set":"Set a configuration value",
        "get":"Get a configuration value",
        "list":"List all configuration keys",
    },
    "settings":{
        "show":"Show current settings",
        "list":"List all settings",
    },
    "model":{
        "list":"List available models",
        "create":"show current model",
    },
    "theme":{
        "dark":"switch dark mode",
        "light":"switch light mode",
    },
    "skills":{
        "list":"List all available skills.",
        "show":"Show details of a skill.",
    },
    "agents":{
        "list":"list all  agents in the graph",
        "show":"Show detailed infromation for a specific agent",
    },
    "plan":{
        "show":"Show current plan",
        "clear":"Clear current plan",
    },
    "save":{
        "session": "Save current session with a name",
    },
    "load":{
        "session":"Load a saved session by ID",
    },
}


class CliCompleter(Completer):

    COMMAND_DESCRIPTIONS = {
        "exit": "Exit the application",
        "help": "Show available commands",
        "reset": "Reset the current session",
        "status": "Show runtime status",
        "config": "Configure RabbitAgent settings (LLM provider, model, etc.)",
        "mode": "View or switch the agent mode",
        "m": "View or switch the agent mode (alias of /mode)",
        "clear": "Clear the screen",
        "multiline": "Toggle multiline input mode",
        "token": "Show token usage",
        "web": "Web search",
        "interrupt": "Interrupt the current operation",
        "sessions": "List and manage saved sessions",
        "save": "Save the current session",
        "load": "Load a saved session",
        "skills": "List available skills",
        "agents": "List available agents",
        "plan": "Enter or manage plan mode",
        "mcp": "MCP server management",
        "settings": "View or edit settings",
        "model": "View or switch the LLM model",
        "theme": "View or switch the UI theme",
        "config-encrypt": "Encrypt sensitive config values",
        "config-decrypt": "Decrypt encrypted config values",
        "config-reencrypt": "Re-encrypt config values (rotate encryption key)",
    }

    def __init__(self,commands:list[str],command_descriptions:dict[str, str]|None=None)->None:
        super().__init__()
        self.commands = commands
        self.command_descriptions = {**self.COMMAND_DESCRIPTIONS}
        if command_descriptions:
            self.command_descriptions.update(command_descriptions)
        self.command_subcommands={command:subcommands.copy() for command,subcommands in COMMAND_SUBCOMMANDS.items()}

    def set_model_completions(self, model_descriptions: dict[str, str] | None) -> None:
        """注册可用模型，供 ``/model <name>`` 做子命令补全。

        :param model_descriptions: ``{模型名: 说明}``；为空时不做任何改动。
        """
        if not model_descriptions:
            return
        self.command_subcommands.setdefault("model", {}).update(model_descriptions)

    def _all_command_names(self) -> list[str]:
        """命令名全集 = 构造传入的 commands + COMMAND_DESCRIPTIONS 的 key（去重，保序）。"""
        seen: set[str] = set()
        names: list[str] = []
        for name in list(self.commands) + list(self.command_descriptions.keys()):
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names
    # 自动生成
    def get_completions(self, document: Document, complete_event: CompleteEvent):
        """
        prompt_toolkit Completer 抽象方法实现。

        两级补全：
        1. 正在输入第一个词 -> 匹配命令名（兼容带 ``/`` 前缀）；
        2. 命令名 + 空格后 -> 匹配该命令的子命令（COMMAND_SUBCOMMANDS）。

        每个 Completion 携带描述（display_meta），便于补全菜单显示用途。
        """
        text = document.text_before_cursor
        stripped = text.rstrip()
        ends_with_space = bool(text) and text[-1] in (" ", "\t")

        # 1. 命令名后紧跟空格 -> 补全该命令的子命令
        if ends_with_space:
            parts = stripped.split()
            if len(parts) == 1:
                cmd = parts[0].lstrip("/")
                yield from self._complete_subcommand(cmd, "")
            return

        # 2. 正在输入当前词
        parts = stripped.split()
        if not parts:
            # 空输入：列出所有命令
            for name in sorted(self._all_command_names()):
                yield Completion(
                    name,
                    start_position=0,
                    display=name,
                    display_meta=self.command_descriptions.get(name, ""),
                )
            return

        last = parts[-1]
        prefix = last.lstrip("/")

        if len(parts) == 1:
            # 补全命令名
            for name in sorted(self._all_command_names()):
                if name.startswith(prefix):
                    yield Completion(
                        name,
                        start_position=-len(prefix),
                        display=name,
                        display_meta=self.command_descriptions.get(name, ""),
                    )
        else:
            # 补全子命令
            cmd = parts[0].lstrip("/")
            yield from self._complete_subcommand(cmd, prefix)

    def _complete_subcommand(self, cmd: str, prefix: str):
        """为命令 ``cmd`` 产出匹配 ``prefix`` 前缀的子命令补全。"""
        subs = self.command_subcommands.get(cmd, {})
        for sub, desc in sorted(subs.items()):
            if sub.startswith(prefix):
                yield Completion(
                    sub,
                    start_position=-len(prefix) if prefix else 0,
                    display=sub,
                    display_meta=desc,
                )