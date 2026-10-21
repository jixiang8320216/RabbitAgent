from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class UpdateFileToolHandler(BaseToolHandler):
    """展示 update_file 工具对文件做的整体更新（全量覆写）。"""

    title = "Update File"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
