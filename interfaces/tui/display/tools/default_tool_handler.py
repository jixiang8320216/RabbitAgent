from __future__ import annotations

from typing import Any

from interfaces.tui.display.tools.base_tool_handler import BaseToolHandler


class DefaultToolHandler(BaseToolHandler):
    """未匹配到专用 Handler 时使用的兜底处理器。

    按指定 ``format``（如 ``json``）序列化任意工具调用数据，
    ``observer`` 为可选的 TUIObserver 引用，用于辅助渲染。
    """

    title = "Tool"

    def __init__(self, format: str = "json", observer: Any = None, title: str = "") -> None:
        super().__init__(title=title)
        self.format = format
        self.observer = observer

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        if self.format == "json":
            import json

            try:
                return json.dumps(tool_input, ensure_ascii=False, default=str, indent=2)
            except (TypeError, ValueError):
                return str(tool_input)
        return str(tool_input)
