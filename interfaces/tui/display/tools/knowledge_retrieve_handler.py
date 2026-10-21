from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class KnowledgeRetrieveHandler(BaseToolHandler):
    """展示 knowledge_retrieve_tool 工具检索到的知识片段。"""

    title = "Knowledge Retrieve"

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        return None
