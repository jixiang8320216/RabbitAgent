from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class GlobToolHandler(BaseToolHandler):
    """展示 global_files（glob 模式匹配）工具返回的文件列表。"""

    title = "Glob Files"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
