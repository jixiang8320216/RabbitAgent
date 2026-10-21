from __future__ import annotations

import copy
import threading
from typing import Any


class RoleContext:
    """角色上下文：按 agent_id 保存该 agent 的角色配置与运行时状态。

    当前启动链只要求可无参构造，并作为实例属性被
    `RoleContextApplicationService` 持有（见 `rabbit_application.py:61`）。
    这里给出最小可用契约：按 agent_id 存取字典，读写互不干扰，
    并提供整体快照，供后续角色编排（roleplay 模式）扩展。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._contexts: dict[str, dict[str, Any]] = {}

    def set(self, agent_id: str, context: dict[str, Any]) -> None:
        """写入指定 agent 的角色上下文（覆盖式）。"""
        with self._lock:
            self._contexts[agent_id] = dict(context)

    def get(self, agent_id: str) -> dict[str, Any] | None:
        """读取指定 agent 的角色上下文；不存在时返回 None。"""
        with self._lock:
            context = self._contexts.get(agent_id)
            return dict(context) if context is not None else None

    def remove(self, agent_id: str) -> None:
        """移除指定 agent 的角色上下文；不存在时静默忽略。"""
        with self._lock:
            self._contexts.pop(agent_id, None)

    def clear(self) -> None:
        with self._lock:
            self._contexts.clear()

    def agent_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._contexts)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """返回全部角色上下文的深拷贝，供持久化/回显使用。"""
        with self._lock:
            return copy.deepcopy(self._contexts)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._contexts

    def __len__(self) -> int:
        return len(self._contexts)
