import logging

from interfaces.commands.markdown_command import MarkdownCommand
from interfaces.commands.protocol import CommandProtocol
from interfaces.commands.discovery import MarkdownCommandInfo, CommandDiscovery
from typing import cast, Dict
from pathlib import Path
from collections.abc import Iterable

logger = logging.getLogger(__name__)


class CommandRegistry:
    def __init__(self, project_home: str = "."):
        self._commands: Dict[str, CommandProtocol] = {}
        self._markdown_commands: Dict[str, MarkdownCommandInfo] = {}
        self._project_home = Path(project_home).resolve()

    def register(self, command: CommandProtocol) -> str:
        """
        注册单个命令实例。

        命令名取命令对象的 ``name`` 属性（如 ``ConfigCommand.name = "config"``）；
        若声明了 ``aliases``，每个别名也会注册到同一个实例上。

        同名命令直接覆盖（与 ``discover_markdown_commands`` 的写入语义一致）。

        :return: 最终注册的命令名（原 docstring 声称返回该值，但实现里没有
            ``return``，调用方拿到的一律是 ``None``；此处补上，与文档一致）。
        """
        self._commands[command.name] = command
        aliases = getattr(command, "aliases", None)
        if isinstance(aliases, Iterable) and not isinstance(aliases, str):
            for alias in aliases:
                if isinstance(alias, str):
                    self._commands[alias] = command
        return command.name

    def list_commands(self) -> list[CommandProtocol]:
        """
        返回已注册命令实例的去重列表（保持插入顺序）。

        因 ``register`` 会为别名多次存入同一对象，
        直接 ``list(self._commands.values())`` 会含重复项，
        这里用 ``dict.fromkeys`` 去重。
        """
        return list(self._commands.values())

    def get(self, name: str) -> CommandProtocol | None:
        """按命令名查询；不存在返回 None。"""
        return self._commands.get(name)

    def get_command(self, name: str) -> CommandProtocol | None:
        """``get`` 的别名，供调用点 ``command_registry.get_command("model")`` 使用。"""
        return self._commands.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._commands

    def __len__(self) -> int:
        return len(self._commands)

    def parse_input(self, user_input: str) -> tuple[str, str, str | list[str]]:
        """把一行用户输入解析成 ``(类型, 命令名, 参数)``。

        类型取值：``"shell"`` / ``"slash"`` / ``"text"``。

        Shell 前缀修复
        --------------
        原判断是 ``user_input.strip() == "!"`` —— **等值比较**，只有光秃秃的
        一个 ``!`` 才会命中。于是 ``!ls`` / ``!echo hi`` 这类**真正的 shell 命令
        全部被当成普通对话文本**（返回 ``("text", "", 原样)``），静默降级、
        不报任何错。而 TUI 状态栏恰恰在宣传 ``! bash`` 这个用法，用户照着提示
        输入却只会得到模型的闲聊回复。

        现在改为 ``startswith("!")``，只要以 ``!`` 开头就识别为 shell 调用。
        """
        user_input = user_input.strip()
        if user_input.startswith("!"):
            # "!ls -la" -> ("shell", "!", "ls -la")；裸 "!" -> 命令体为空
            return ("shell", "!", user_input[1:].strip())
        if user_input.startswith("/"):
            parts = user_input[1:].split()
            command_name=parts[0] if parts else ""
            args=parts[1:] if len(parts) > 1 else []
            return("slash", command_name, args)
        return ("text","", user_input)

    def discover_markdown_commands(self) -> None:
        discovery = CommandDiscovery(str(self._project_home))
        discovered = discovery.discover_all()

        # store markdown command info for reference
        self._markdown_commands = discovered

        for cmd_name, cmd_info in discovered.items():
            markdown_cmd = MarkdownCommand(cmd_info)
            self._commands[cmd_name] = cast(CommandProtocol, markdown_cmd)
            logger.debug(f"[CommandRegistry] Registered  markdown command: {cmd_name}")
        logger.info(f"[CommandRegistry] Discovered and Registered {len(discovered)} markdown commands")
