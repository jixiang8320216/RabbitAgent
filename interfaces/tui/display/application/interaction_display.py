from rich.text import Text
from textual.widgets import Static


class InteractionDisplay:

    def __init__(self,app):
        self.app = app

    def render_retry_status(
        self,
        agent_name: str,
        attemps: int,
        max_attempts: int,
        delay: float,
        error: str,
        error_type: str,
        depth: int=0,
    )->None:
        indent= self._indent(depth)

        #注意：``Text.assemble`` 的每个片段都必须是 ``(text, style)`` 二元组。
        # 原代码第一段 ``(f"...","yellow")`` 后面漏了逗号，Python 会把相邻两个
        # 元组做「隐式拼接」再当成一个参数传进去，import 这个模块时就会报
        # SyntaxWarning: 'tuple' object is not callable（调用时表现为
        # "'tuple' object is not callable"），重试状态因此完全渲染不出来。
        status_text =Text.assemble(
            (f"{indent}[dim]{agent_name}","yellow"),
            ("遇到错误，正在重试", "yellow"),
            (f"{attemps}/{max_attempts}", "dim"),
            (f", 将在{delay:.0f}s 后重试...", "yellow"),
        )

        interrupt_hint=Text.assemble(
            (f"{indent} ", "dim"),
            ("[按ESC 中断]", "dim italic"),
        )
        error_preview=error[:100]+"..." if len(error) > 100 else error
        error_text=Text.assemble(
            (f"{indent}  错误: ", "dim"),
            (error_preview, "red"),
        )

        self.app.add_widget(Static(status_text))
        self.app.add_widget(Static(interrupt_hint))
        self.app.add_widget(Static(error_text))

    def render_agent_call_end(
        self,
        agent_name: str,
        duration_ms: float,
        error: str=None,
        depth: int=1,
    )->None:

        indent = self._indent(depth)
        if error:
            msg= Text.assemble(
                 (f"{indent} {agent_name}","bold red"),
                 ("failed", "red"),
                 (f"({duration_ms:.0f}ms)", "dim"),
            )
            self.app.add_widget(Static(msg))
            self.app.add_widget(Static(Text(f"{indent}Error: {error}", "red")))

    @staticmethod
    def _indent(depth: int)->str:
        """按调用深度给出缩进（子 agent 越深越往右，最深 8 级，避免顶到屏边）。"""
        try:
            level = int(depth)
        except (TypeError, ValueError):
            level = 0
        return "  " * min(max(level, 0), 8)

    @staticmethod
    def _format_duration(duration_ms: int)->str:
        """毫秒转人类可读时长：小于 1s 用 ``ms``，否则用 ``s``（一位小数）。"""
        try:
            total_ms = int(duration_ms)
        except (TypeError, ValueError):
            return "0ms"
        if total_ms < 0:
            total_ms = 0
        if total_ms < 1000:
            return f"{total_ms}ms"
        return f"{total_ms / 1000:.1f}s"

    @staticmethod
    def _first_line(text: str|None, max_chars: int=160)->str:
        """取错误信息的第一行（截断到 ``max_chars``），并转义 rich 的方括号标记。

        错误文本里常带 ``[...]``，不转义会被 rich 当成样式标记而渲染错乱（或直接报
        markup 错误）；这里统一转成字面方括号。
        """
        if not text:
            return ""
        line = str(text).strip().splitlines()
        if not line:
            return ""
        first = line[0].strip()
        if len(first) > max_chars:
            first = first[:max_chars].rstrip() + "..."
        return first.replace("[", "\\[").replace("]", "\\]")
