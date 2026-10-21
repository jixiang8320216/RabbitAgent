from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class ExecuteCommandHandler(BaseToolHandler):
    """展示 execute_command 工具的执行过程与结果（命令、输出、退出码）。"""

    title = "Execute Command"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
