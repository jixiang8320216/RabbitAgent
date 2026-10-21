from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class EditFileHandler(BaseToolHandler):
    """展示 edit_file 工具对文件做的局部编辑（旧片段 -> 新片段）。"""

    title = "Edit File"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
