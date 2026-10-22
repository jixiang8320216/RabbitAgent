"""持久化域的存储端口。

本模块只放**结构契约**（Protocol），不含任何实现。

已有端口：
- ``SessionStateStore``：会话状态存储。
- ``SessionBackupStore``：会话备份存储（供恢复流程使用）。
"""

from __future__ import annotations

import typing


@typing.runtime_checkable
class SessionStateStore(typing.Protocol):
    ...


@typing.runtime_checkable
class SessionBackupStore(typing.Protocol):
    """会话备份存储端口。

    用途：会话状态写坏或需要回滚时，从备份恢复。形状依据
    ``domains.session.infrastructure.session_persistence`` 的注入点反推：
    该端口是**可选依赖**（可为 None），未提供时持久化层退化为「只写不备份」，
    不影响主流程。
    """

    def save_backup(self, session_id: str, payload: typing.Any) -> None:
        """保存一份会话备份。"""
        ...

    def load_backup(self, session_id: str) -> typing.Any | None:
        """读取会话备份；不存在返回 None。"""
        ...

    def list_backups(self) -> typing.Sequence[str]:
        """列出全部备份的 session_id。"""
        ...


@typing.runtime_checkable
class SessionSnapshotStore(typing.Protocol):
    """会话快照存储端口（形状对齐 ``FileSessionSnapshotStore``）。

    用途：在会话目录下以 ``snapshot.json`` 保存/读取会话快照（默认记录、
    agent 历史等）。该端口是**可选依赖**（可为 None），未提供时运行时退化为
    「无快照」，不影响主流程。
    """

    def save(self, session_id: str, snapshot: typing.Any) -> None:
        """写入会话快照。"""
        ...

    def load(self, session_id: str) -> typing.Any | None:
        """读取会话快照；缺失或损坏时返回 None。"""
        ...

    def delete(self, session_id: str) -> None:
        """删除会话快照。"""
        ...

    def list_session_ids(self) -> typing.Sequence[str]:
        """列出已有快照的 session_id。"""
        ...


__all__ = ["SessionBackupStore", "SessionStateStore", "SessionSnapshotStore"]