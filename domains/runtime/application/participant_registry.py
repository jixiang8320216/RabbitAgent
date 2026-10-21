"""参与者注册表。

「参与者」（participant）是会话中除主 Agent 之外的可寻址实体：子 Agent、
命名专家、外部接入的成员等。它们都需要一个稳定的 ``participant_id`` 才能在
消息路由、群聊（groupchat）和记忆中互相引用。

本模块只负责**登记与查找**：

- 登记时只做规范化与去重，不校验参与者是否真实存在（校验属于各自领域的职责）；
- 查找失败返回 ``None`` 而不是抛错，让路由层自行决定降级策略；
- 线程安全（群聊/多 Agent 并发访问同一注册表）。

之所以默认无参构造：``RabbitApplicationCompositionState`` 里是
``participant_registry_module.ParticipantRegistry()``，注册内容由后续的
会话初始化流程填充。
"""

from __future__ import annotations

import collections.abc
import threading
import typing
from dataclasses import dataclass, field

__all__ = ["Participant", "ParticipantRegistry"]


@dataclass(frozen=True)
class Participant:
    """一个参与者的登记信息。

    :param participant_id: 会话内唯一标识，作为注册表的键。
    :param kind: 参与者类别，例如 ``"agent"`` / ``"user"`` / ``"external"``。
    :param display_name: 展示名，用于回显与日志。
    :param metadata: 开放字段，承载各自领域的附加信息。
    """

    participant_id: str = ""
    kind: str = ""
    display_name: str = ""
    metadata: typing.Mapping[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "participant_id", str(self.participant_id or ""))
        object.__setattr__(self, "kind", str(self.kind or ""))
        object.__setattr__(self, "display_name", str(self.display_name or ""))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


class ParticipantRegistry:
    """参与者的进程内注册表（线程安全）。"""

    __slots__ = ("_participants", "_lock")

    def __init__(self, participants: typing.Iterable[Participant | typing.Any] | None = None) -> None:
        self._participants: dict[str, Participant] = {}
        self._lock = threading.RLock()
        for item in participants or ():
            self.register(item)

    def register(
        self,
        participant: Participant | typing.Any,
        *,
        kind: str | None = None,
        display_name: str | None = None,
    ) -> Participant | None:
        """登记（或覆盖）一个参与者。

        :param participant: :class:`Participant`，或任何具备 ``participant_id`` /
            ``agent_id`` / ``name`` 之一的对象。
        :param kind: 显式类别，覆盖对象自带值。
        :param display_name: 显式展示名，覆盖对象自带值。
        :return: 实际入库的参与者；无法解析出标识时返回 ``None``（不抛错）。
        """
        entry = _coerce_participant(participant, kind=kind, display_name=display_name)
        if entry is None or not entry.participant_id:
            return None
        with self._lock:
            self._participants[entry.participant_id] = entry
        return entry

    def unregister(self, participant_id: str) -> bool:
        """注销参与者；返回是否确实删除了条目。"""
        with self._lock:
            return self._participants.pop(str(participant_id), None) is not None

    def get(self, participant_id: str) -> Participant | None:
        """按标识查找参与者；不存在返回 ``None``。"""
        with self._lock:
            return self._participants.get(str(participant_id))

    def all_participants(self) -> tuple[Participant, ...]:
        """返回全部已登记参与者。"""
        with self._lock:
            return tuple(self._participants.values())

    def ids(self) -> tuple[str, ...]:
        """返回全部参与者标识（保持插入顺序）。"""
        with self._lock:
            return tuple(self._participants.keys())

    def clear(self) -> None:
        """清空注册表（会话结束时调用）。"""
        with self._lock:
            self._participants.clear()

    def __contains__(self, participant_id: object) -> bool:
        with self._lock:
            return str(participant_id) in self._participants

    def __len__(self) -> int:
        with self._lock:
            return len(self._participants)

    def __repr__(self) -> str:
        return f"ParticipantRegistry(count={len(self)})"


def _coerce_participant(
    participant: typing.Any,
    *,
    kind: str | None,
    display_name: str | None,
) -> Participant | None:
    """把任意入参归一为 :class:`Participant`。"""
    if participant is None:
        return None
    if isinstance(participant, Participant):
        if kind is None and display_name is None:
            return participant
        return Participant(
            participant_id=participant.participant_id,
            kind=kind if kind is not None else participant.kind,
            display_name=display_name if display_name is not None else participant.display_name,
            metadata=participant.metadata,
        )

    if isinstance(participant, str):
        return Participant(
            participant_id=participant,
            kind=str(kind or ""),
            display_name=str(display_name or ""),
        )

    if isinstance(participant, collections.abc.Mapping):
        identifier = (
            participant.get("participant_id")
            or participant.get("agent_id")
            or participant.get("name")
        )
        if not identifier:
            return None
        return Participant(
            participant_id=str(identifier),
            kind=str(kind or participant.get("kind", "") or ""),
            display_name=str(display_name or participant.get("display_name", "") or ""),
            metadata=dict(participant.get("metadata", None) or {}),
        )

    identifier = (
        getattr(participant, "participant_id", None)
        or getattr(participant, "agent_id", None)
        or getattr(participant, "name", None)
    )
    if not identifier:
        return None
    return Participant(
        participant_id=str(identifier),
        kind=str(kind or getattr(participant, "kind", "") or ""),
        display_name=str(display_name or getattr(participant, "display_name", "") or ""),
        metadata=dict(getattr(participant, "metadata", None) or {}),
    )
