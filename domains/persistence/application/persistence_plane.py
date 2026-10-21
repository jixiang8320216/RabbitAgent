from __future__ import annotations

import threading
from typing import Any
import domains.persistence.domain.store_ports as ports


class PersistencePlane:
    """持久化平面：把一类持久化后端（文件 / 数据库等）的各个 store 聚合在一起。

    `rabbits_application_composition` 通过它取用 hook 相关存储
    （`hook_state` / `hook_run` / `session_event`）。任一 store 为 None 时，
    hook 运行时会整体降级（只记录 warning），因此这里的字段允许为 None，
    以支持「能力未就绪即降级」的启动策略。
    """

    def __init__(
        self,
          session_state= ports.SessionStateStore|None,
        *,
        hook_state: Any = None,
        hook_run: Any = None,
        session_event: Any = None,
    ) -> None:
        self.session_state = session_state
        self._lock = threading.RLock()
        self.hook_state = hook_state
        self.hook_run = hook_run
        self.session_event = session_event

    def is_hook_ready(self) -> bool:
        """三个 hook 相关 store 是否齐备（供调用方判断能否装配 hook 运行时）。"""
        return (
            self.hook_state is not None
            and self.hook_run is not None
            and self.session_event is not None
        )

    def __repr__(self) -> str:
        return (
            "PersistencePlane("
            f"hook_state={type(self.hook_state).__name__}, "
            f"hook_run={type(self.hook_run).__name__}, "
            f"session_event={type(self.session_event).__name__})"
        )
