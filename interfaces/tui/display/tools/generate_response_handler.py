from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class GenerateResponseHandler(BaseToolHandler):
    """展示 generate_response 工具最终生成的响应文本（流式/整段）。"""

    title = "Generate Response"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
