"""应用级展示件：banner / 欢迎信息一类「整屏级」渲染。"""

from __future__ import annotations

import logging

from textual.widgets import Static

from interfaces.tui.widgets.tui_app import TUIApp

logger = logging.getLogger(__name__)

__all__ = ["ApplicationDisplay"]

_CHAT_AREA_SELECTOR = "#chat-area"


class ApplicationDisplay:

    def __init__(self, app:TUIApp):
        self.app = app

    def render_banner(self, version: str) -> None:
        """在聊天区顶部渲染欢迎 banner。

        由 ``TUIObserver._render_banner_after_ready`` 在 app 就绪事件置位后调用。
        聊天区还没挂上时（``#chat-area`` 查询不到）只记 debug 并跳过——banner
        属于装饰性输出，缺失不该影响主循环。
        """
        text = self._build_banner_text(version)
        try:
            chat_area = self.app.query_one(_CHAT_AREA_SELECTOR)
        except Exception:
            logger.debug("chat area is not mounted yet, banner skipped", exc_info=True)
            return
        try:
            chat_area.mount(Static(text, id="welcome-banner", classes="banner"))
        except Exception:
            logger.debug("failed to mount welcome banner", exc_info=True)

    def render_error(
        self,
        source: str,
        error_msg: str,
        details: str | None = None,
    ) -> None:
        """在聊天区渲染一条错误块。

        由 ``TUIObserver.on_error`` 调用。该方法此前缺失，导致任何错误上报都会
        抛 ``AttributeError: 'ApplicationDisplay' object has no attribute
        'render_error'``——真正的故障被二次异常吞掉，界面上什么都看不到。
        与 ``render_banner`` 同样：聊天区未挂载时只记 debug，绝不打断主循环。
        """
        text = self._build_error_text(source, error_msg, details)
        try:
            chat_area = self.app.query_one(_CHAT_AREA_SELECTOR)
        except Exception:
            logger.debug("chat area is not mounted yet, error block skipped", exc_info=True)
            return
        try:
            chat_area.mount(Static(text, classes="error-msg"))
        except Exception:
            logger.debug("failed to mount error block", exc_info=True)

    @staticmethod
    def _build_error_text(source: str, error_msg: str, details: str | None) -> str:
        lines = [f"[{source}] {error_msg}"]
        if details:
            lines.append(str(details))
        # 转义 markup 起始符，避免错误文本被当成 Rich 标签解析
        return "\n".join(lines).replace("[", "\\[")

    @staticmethod
    def _build_banner_text(version: str) -> str:
        lines = [
            f"Rabbit Agent v{version}",
            "输入消息开始对话 · /help 查看命令 · Esc 中断 · F2 复制模式",
        ]
        return "\n".join(lines)
