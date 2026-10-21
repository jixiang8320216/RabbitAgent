from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class WriteFileHandler(BaseToolHandler):
    """展示 write_file 工具写入的文件路径与内容摘要。"""

    title = "Write File"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
