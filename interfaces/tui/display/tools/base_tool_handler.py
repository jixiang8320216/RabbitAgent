from __future__ import annotations

from typing import Any


class BaseToolHandler:
    """所有工具展示处理器的基类。

    TUIObserver 维护一个 ``tool_handlers`` 字典，按工具名分发到对应 Handler；
    每种工具的展示逻辑由子类在 :meth:`handle` 中实现。基类提供 ``title``
    字段（用于面板标题）和一个默认空实现的 :meth:`handle`，子类按需重写。
    """

    title: str = ""

    def __init__(self, title: str = "") -> None:
        # 允许子类通过类属性声明默认 title，实例构造时也可覆盖
        if title:
            self.title = title

    def handle(self, tool_input: Any, *, context: Any = None, **kwargs: Any) -> Any:
        """处理一次工具调用事件并产出展示内容。

        :param tool_input: 工具调用数据（工具名、参数、结果等）。
        :param context: TUIObserver / 渲染上下文，可为 None。
        :return: 由子类决定，默认返回 None（占位实现，不产出展示）。
        """
        return None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(title={self.title!r})"
