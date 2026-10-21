"""轮次检查点协调器（SessionTurnCheckpointCoordinator）。

``SessionOrchestrator`` 在一轮开始/结束时会调用它（``_prepare_turn_identity`` /
``_publish_turn_checkpoint``），用于工作区版本快照与回滚。

被真实调用到的两个入口：

- ``SessionTurnCheckpointCoordinator._prepare_turn_identity(user_msg, session_id)``
  —— 静态方法，**必须存在**，否则每轮一开始必 ``AttributeError``
  （``session_orchestrator.py:355``）；
- ``publish_turn_checkpoint(session_id, *, turn_id, boundary, user_msg)``
  —— 实例方法，供 ``SessionOrchestrator._turn_checkpoint_coordinator()`` 返回的
  对象使用（``session_orchestrator.py:349``）。

检查点属于**旁路能力**：写不进去、后端没接，都不应该阻断一轮对话。所以所有
失败都只记 debug 日志，绝不向上抛。

``boundary`` / ``boundry`` 两种拼写都要收——调用方与协调器内部的历史拼写不一致
（``session_turn_execution`` 传 ``boundary``，``session_orchestrator`` 曾声明
``boundry``），这里用别名兼容，避免哪天统一拼写时又炸一次。
"""

from __future__ import annotations

import logging
import typing
import uuid

logger = logging.getLogger(__name__)

__all__ = [
    "SessionTurnCheckpointCoordinator",
    "TurnCheckpoint",
]

_LOG_PREFIX = "[TURN-CKPT] "


class TurnCheckpoint:
    """一条轮次检查点记录（内存表示，便于后续对接真实存储）。"""

    __slots__ = ("session_id", "turn_id", "boundary", "user_message_id")

    def __init__(
        self,
        session_id: str,
        turn_id: str,
        boundary: object,
        user_message_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.boundary = boundary
        self.user_message_id = user_message_id

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TurnCheckpoint(session={self.session_id!r}, turn={self.turn_id!r}, "
            f"boundary={self.boundary!r})"
        )


class SessionTurnCheckpointCoordinator:
    """轮次标识分配 + 检查点发布。

    :param sink: 可选的落盘/发布目标，需提供 ``publish(checkpoint)``；
        缺省时只记内存，便于在真实存储接入前不阻塞链路。
    """

    def __init__(self, sink: typing.Any = None) -> None:
        self._sink = sink
        self._records: list[TurnCheckpoint] = []

    # --- 轮次标识 ----------------------------------------------------------

    @staticmethod
    def _prepare_turn_identity(user_msg: object, session_id: str) -> str:
        """确定本轮的 ``turn_id``。

        用户消息上已经带了 ``metadata["turn_id"]`` 就沿用——上游可能已经落库，
        重新生成会让两边标识脱节。否则新分配一个。
        """
        metadata = getattr(user_msg, "metadata", None)
        if isinstance(metadata, typing.Mapping):
            existing = metadata.get("turn_id")
            if isinstance(existing, str) and existing.strip():
                return existing.strip()
        return str(uuid.uuid4())

    # --- 检查点发布 --------------------------------------------------------

    async def publish_turn_checkpoint(
        self,
        session_id: str,
        *,
        turn_id: str,
        boundary: object = None,
        user_msg: object = None,
        boundry: object = None,
        **extra: object,
    ) -> TurnCheckpoint | None:
        """发布一条检查点；失败只记日志。

        ``boundry`` 是历史拼写，作为 ``boundary`` 的兜底别名。
        """
        resolved_boundary = boundary if boundary is not None else boundry
        checkpoint = TurnCheckpoint(
            session_id=str(session_id or ""),
            turn_id=str(turn_id or ""),
            boundary=resolved_boundary,
            user_message_id=self._message_id(user_msg),
        )
        self._records.append(checkpoint)
        if self._sink is None:
            logger.debug(
                "%sno sink wired; checkpoint kept in memory: %r", _LOG_PREFIX, checkpoint
            )
            return checkpoint
        try:
            produced = self._sink.publish(checkpoint)
        except Exception as exc:  # noqa: BLE001 - 旁路能力，失败不该阻断一轮对话
            logger.debug("%spublish failed: %s", _LOG_PREFIX, exc)
            return checkpoint
        if hasattr(produced, "__await__"):
            try:
                await produced
            except Exception as exc:  # noqa: BLE001
                logger.debug("%sasync publish failed: %s", _LOG_PREFIX, exc)
        return checkpoint

    @staticmethod
    def _message_id(user_msg: object) -> str | None:
        identifier = getattr(user_msg, "id", None)
        return str(identifier) if identifier is not None else None

    # --- 诊断 --------------------------------------------------------------

    def records(self) -> list[TurnCheckpoint]:
        return list(self._records)

    def __repr__(self) -> str:  # pragma: no cover
        return f"SessionTurnCheckpointCoordinator(records={len(self._records)})"
