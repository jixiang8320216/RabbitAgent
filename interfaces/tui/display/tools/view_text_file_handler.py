from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class ViewTextFileHandler(BaseToolHandler):
    """展示 read_file 工具读取到的文本文件内容。"""

    title = "Read File"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
