"""Agent 生成清单（spawn manifest）的持久化。

清单记录「一次 agent 生成（spawn）留下了什么」——生成参数、初始提示、
产出位置等。它有两个用途：

1. **审计**：事后能回答「这个 agent 是怎么来的」。
2. **恢复**：会话恢复时按清单重建 agent 树。

存储形态：每个 session 一个 JSON 文件，位于 ``<sessions_dir>/<session_id>.manifest.json``。
选 JSON 而非数据库，是因为清单天然按 session 聚合、整体读写，没有跨 session
查询需求；文件形态也便于人工排查。

线程安全：
    多个 agent 可能并发写同一 session 的清单（spawn 是并发的）。用
    ``RLock`` 保护「读-改-写」临界区，避免后写覆盖先写。
"""

from __future__ import annotations

import json
import pathlib
import threading
import typing


class AgentSpawnManifestRepository:
    """按 session 维度读写 agent 生成清单的仓库。"""

    def __init__(self, sessions_dir: str | pathlib.Path) -> None:
        self._sessions_dir = pathlib.Path(sessions_dir)
        self._lock = threading.RLock()

    # ── 路径 ────────────────────────────────────────────────────
    def _path_for(self, session_id: str) -> pathlib.Path:
        return self._sessions_dir / f"{session_id}.manifest.json"

    # ── 读 ──────────────────────────────────────────────────────
    def load(self, session_id: str) -> list[dict[str, typing.Any]]:
        """读取某 session 的生成清单。

        :return: 清单条目列表；文件不存在或损坏时返回空列表
            （清单是辅助信息，损坏不应阻断 agent 树的恢复）。
        """
        path = self._path_for(session_id)
        with self._lock:
            if not path.exists():
                return []
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                return []
        if not raw.strip():
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            entries = payload.get("entries")
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, dict)]
        return []

    # ── 写 ──────────────────────────────────────────────────────
    def save(self, session_id: str, entries: typing.Sequence[dict[str, typing.Any]]) -> None:
        """覆盖写入某 session 的生成清单。

        :param entries: 清单条目列表。
        """
        path = self._path_for(session_id)
        payload = [dict(entry) for entry in entries]
        with self._lock:
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子替换，避免写到一半崩溃留下半截 JSON
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp.replace(path)

    def append(self, session_id: str, entry: dict[str, typing.Any]) -> None:
        """追加一条清单条目。"""
        with self._lock:
            entries = self.load(session_id)
            entries.append(dict(entry))
            self.save(session_id, entries)


__all__ = ["AgentSpawnManifestRepository"]