from __future__ import annotations

import logging
import time
from rich.spinner import  Spinner
from rich.text import Text

from interfaces.tui.widgets.tui_app import TUIApp

logger = logging.getLogger(__name__)

__all__ = ["ThinkingHandler"]

_PREVIEW_LINES = 8
_PREVIEW_CHARS = 600


class ThinkingHandler:

    def __init__(self, app:TUIApp):
        self.app = app
        self._spinner = Spinner("dots",style="cyan")

    def render_update(self, agent_name: str, thought: str , ) -> Spinner:
        lines=[line.strip() for line in thought.split("\n") if line.strip()]
        if lines:
            last_line=(str(lines[-1]).replace("'''","").replace("#","").replace("*",""))
            display_prefix="Thinking: "
            display_text=f"{display_prefix}{last_line}"
        else:
            display_text=f"{agent_name} is thinking"

        self._spinner.text = Text(f" {display_text}", style="cyan")

        return self._spinner

