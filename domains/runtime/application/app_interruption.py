"""应用级中断协调器。

``RabbitApplication._interruption_coordinator()`` 构造并返回本协调器，用于按会话
粒度中断正在进行的轮次 / 后台 agent。当前实现是最小可用版本：解析会话、打一次性
中断标记、取消当前轮次作用域——具体「按 agent_id 精确定位到进程/任务」的拦截随实例
存储一起落地。

构造签名与 ``rabbit_application._interruption_coordinator`` 的调用点保持一致
（``app`` + 一组命名依赖），便于宿主未来替换更精细的实现而不动调用方。
"""

from __future__ import annotations

import logging
import typing

import pydantic

logger = logging.getLogger(__name__)


class AppInterruptionCoordinator:
    """后台 agent / 轮次中断协调器。"""

    def __init__(
        self,
        app: typing.Any,
        *,
        session_id_resolver: typing.Callable[[], typing.Optional[str]],
        turn_interrupter: typing.Callable[..., typing.Any],
        turn_locks: typing.Mapping[str, typing.Any],
        turn_locks_guard: typing.Any,
        in_flight_interruptes: typing.Mapping[str, typing.Any],
    ) -> None:
        self._app = app
        self._session_id_resolver = session_id_resolver
        self._turn_interrupter = turn_interrupter
        self._turn_locks = turn_locks
        self._turn_locks_guard = turn_locks_guard
        self._in_flight_interrupts = in_flight_interruptes

    async def interrupt_background_agent(
        self,
        agent_id: str,
        *,
        reason: str = "user_stop_agent",
    ) -> dict[str, pydantic.JsonValue]:
        from domains.permission.application.interrupt_coordination import InterruptManager

        summary: dict[str, pydantic.JsonValue] = {
            "agent_id": str(agent_id),
            "reason": reason,
            "session_id": None,
            "interrupt_requested": False,
            "turn_scope_cancelled": False,
        }
        session_id = self._session_id_resolver()
        summary["session_id"] = session_id
        if not session_id:
            logger.debug("interrupt %r without session id; nothing to interrupt", agent_id)
            return summary
        try:
            InterruptManager.request_interrupt(session_id)
            summary["interrupt_requested"] = True
        except Exception as exc:  # noqa: BLE001 - 中断是尽力而为
            logger.debug("request interrupt failed: %s", exc)
        scope = InterruptManager.get_turn_scope(session_id)
        if scope is not None:
            try:
                scope.cancel()
                summary["turn_scope_cancelled"] = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("cancel turn scope failed: %s", exc)
        return summary
