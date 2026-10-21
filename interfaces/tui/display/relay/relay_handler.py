from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from rich.text import Text
from rich.markdown import  Markdown
from rich.console import Group
from interfaces.tui.display.render_utils import split_think_content, format_rabbit_footer
from interfaces.tui.display.thinking.thinking_handler import ThinkingHandler

logger = logging.getLogger(__name__)

__all__ = ["ReplayHandler"]


class ReplayHandler:
    def __init__(self,
        thinking_handler:Optional[ThinkingHandler]=None,
        on_thinking_end: Optional[Callable[[str,str,float], None]]=None,
        )->None:
        self.thinking_handler = thinking_handler
        self.on_thinking_end = on_thinking_end
        self._thinking_completed =False
        self._thinking_start_time : Optional[float]=None
        self._last_reply_text:str=""

    # --- 展示出口 --------------------------------------------------------

    def render_update(self, text:str)->Text:
        """流式刷新回复正文，返回给 ``Static`` / ``safe_update``。

        调用点：``TUIObserver.on_agent_text_stream``（每来一段就重绘同一块
        live 区）与 ``_finalize_agent_response_once``（补一轮最终文本）。

        两件必须在这里做完的事：

        1. **首次进入回复段就给思考段收尾**——思考块和回复块是两块 live 区，
           谁后发现谁负责通知前一个结束；``_thinking_completed`` 保证只收一次。
           thinking handler 的返回值这里**丢弃**：那一块的 widget 由调用方
           在切换 ``_active_block_id`` 时自行处理，本函数不碰 widget 树。
        2. **打点** ``_thinking_start_time``（本轮回复的开始时刻），
           ``render_end`` 在调用方没给真实耗时（现在写死传 ``0``）时用它兜底。

        ``text`` 是**累积全文**而不是增量片段，所以这里整体重绘即可，不需要
        自己维护 buffer。
        """
        think,reply= split_think_content(text)
        if think is not None and self._thinking_start_time is None:
            self._thinking_start_time = time.time()

        if reply and not self._thinking_completed:
            duration_ms=(time.time() - self._thinking_start_time) * 1000 if self._thinking_start_time else 0.0
            if self.on_thinking_end:
                self.on_thinking_end("Rabbit", think or "", duration_ms)
            self._thinking_completed = True

        if (think is not None) and (not reply) and self.thinking_handler:
            return self.thinking_handler.render_update("Rabbit",think)

        self._last_reply_text=reply.strip()
        prefix = Text.assemble(("Rabbit","bold cyan"),(" > ", "default"))
        return Group(prefix, Markdown(self._last_reply_text))

    def render_end(
        self,
        duration_ms:float,
        current_renderable:Any,
        tokens:int,
        max_tokens:int,
    )->Any:
        """回复收尾：正文 + 一行统计脚注。

        :param duration_ms: 本轮回复耗时（毫秒）。调用方目前写死传 ``0``，
            此时用 ``_thinking_start_time`` 到现在的真实经过时间兜底。
        :param content: 当前 live 区的 ``Static.content``——通常就是上一次
            :meth:`render_update` 的返回值。取不到正文时退回
            ``_last_reply_text``，保证屏幕上不出现空回复。
        :param tokens / max_tokens: token 用量，用于脚注；``max_tokens<=0``
            时只显示 token 数，避免出现 ``123/0`` 这种没有意义的比值。
        :return: ``Group(正文, 空行, 脚注)``——交给调用方去 ``safe_update``。

        同时把 ``on_thinking_end`` 回调触发一次（``(正文, ISO 时间戳, 秒)``），
        用于把思考/回复结果交给上层持久化。回调抛异常只记日志。
        """

        footer = Text(format_rabbit_footer(duration_ms),style="dim")
        prefix = Text.assemble(("Rabbit","bold cyan"),(" > ", "default"))
        renderable = Group(prefix,Markdown(self._last_reply_text))
        return Group(renderable, footer)

