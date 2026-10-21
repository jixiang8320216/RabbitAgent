from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class GrepToolHandler(BaseToolHandler):
    """展示 grep_content 工具检索到的匹配行与上下文。"""

    title = "Grep Content"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
