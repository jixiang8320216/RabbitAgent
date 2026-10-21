from __future__ import annotations

import json
import logging
import pathlib
import threading
from typing import Any

logger = logging.getLogger(__name__)


class FileSessionSnapshotStore:
    """基于文件系统的会话快照存储。

    在每个会话目录下以 ``snapshot.json`` 保存该会话的快照数据
    （会话默认记录、agent 历史等）。写入采用「先写临时文件再替换」，
    避免中途失败留下半截文件。

    读取失败（文件缺失或 JSON 损坏）降级为 None / 空结果，
    不向调用方抛异常。
    """

    _FILE_NAME = "snapshot.json"

    def __init__(self, sessions_dir: str | pathlib.Path) -> None:
        self.sessions_dir = pathlib.Path(sessions_dir)
        self._lock = threading.RLock()

    def _path_for(self, session_id: str) -> pathlib.Path:
        return self.sessions_dir / session_id / self._FILE_NAME

    def save(self, session_id: str, snapshot: dict[str, Any]) -> None:
        """写入会话快照；目录不存在时自动创建。"""
        with self._lock:
            path = self._path_for(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(".json.tmp")
            temp_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)

    def load(self, session_id: str) -> dict[str, Any] | None:
        """读取会话快照；缺失或损坏时返回 None。"""
        with self._lock:
            path = self._path_for(session_id)
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("failed to read session snapshot: %s", path)
                return None
            return payload if isinstance(payload, dict) else None

    def delete(self, session_id: str) -> None:
        """删除会话快照；不存在时静默忽略。"""
        with self._lock:
            path = self._path_for(session_id)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("failed to delete session snapshot: %s", path)

    def list_session_ids(self) -> tuple[str, ...]:
        """列出已有快照的 session_id（按目录名排序）。"""
        if not self.sessions_dir.is_dir():
            return ()
        ids: list[str] = []
        for session_dir in sorted(self.sessions_dir.iterdir()):
            if session_dir.is_dir() and (session_dir / self._FILE_NAME).is_file():
                ids.append(session_dir.name)
        return tuple(ids)

    def __contains__(self, session_id: str) -> bool:
        return self._path_for(session_id).is_file()
