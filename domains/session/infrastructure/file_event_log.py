"""App V3 事件日志持久化。

TUI 的每一次流式文本增量、工具调用、状态变更都会以「事件」形式交给
:class:`AppV3EventPersistence`，由它按会话追加写入 JSONL 事件日志，并返回一个
自增的 ``version_id`` 供上层做乐观并发/顺序校验。

设计要点
--------

- **每会话一个文件**：``sessions_dir/<session_id>.jsonl``，一条事件一行，
  便于 tail -f 观察与崩溃后恢复。
- **version_id 单调递增**：由该会话文件已有行数推导（``行数 + 1``），
  不额外维护索引文件，避免双写不一致。
- **写入容错**：持久化属旁路能力，任何 I/O 异常向上抛，但调用方
  ``tui_observer._persist_event`` 已用 try/except 兜住，不会影响主链路。

为什么需要补这个方法
--------------------

``tui_observer._persist_event`` 在装配处拿到的是本类实例（见
``cli/tui_application.py`` 的 ``AppV3EventPersistence(sessions_dir)``），
但本类此前只有 ``__init__``，没有 ``persist``，导致每条事件都抛::

    AttributeError: 'AppV3EventPersistence' object has no attribute 'persist'

该异常被调用方的 ``except Exception`` 吞掉，只在日志里留一行 ERROR——
表现是「TUI 一切正常，但会话历史文件始终为空」。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


class AppV3EventPersistence:
    """按会话追加写JSONL 事件日志。"""

    def __init__(self, sessions_dir: str | Path) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        # 每个会话已写入的事件条数 -> 下一个 version_id。
        # 必须在内存里记：若每次 persist 都重开文件数行数，流式事件高频写入下
        # 是O(n^2)，且同步文件 I/O 直接阻塞事件循环——实测 TUI 单轮问答被拖到
        # 100 秒以上，LLM 请求迟迟发不出去。启动时按已有文件预热一次即可。
        self._version_counters: dict[Path, int] = {}

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    def session_path(self, session_id: str) -> Path:
        """返回某会话的事件日志路径。

        ``session_id`` 来自会话编排，正常是 UUID。但事件数据里的字段可能
        带路径分隔符或非法字符，这里做一次净化，避免写到sessions_dir 之外。
        """
        safe_id = "".join(
            char if (char.isalnum() or char in "-_") else "_"
            for char in str(session_id)
        ) or "unknown"
        return self.sessions_dir / f"{safe_id}.jsonl"

    def read_events(self, session_id: str) -> list[dict[str, Any]]:
        """读回某会话的全部事件（按写入顺序）。损坏行会被跳过。"""
        path = self.session_path(session_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def persist(
        self,
        session_id: str,
        message: dict[str, Any],
    ) -> str:
        """追加一条事件，返回本次写入的 ``version_id``。

        :param session_id: 会话标识，决定写入哪个 JSONL 文件。
        :param message: 事件负载，须可JSON 序列化；非dict 会被包成
            ``{"payload": message}``。
        :return: 自增版本号字符串（该会话此前已有事件数 + 1）。
        """
        path = self.session_path(session_id)
        version_id = self._next_version_id(path)
        record: dict[str, Any] = {
            "version_id": version_id,
            "session_id": session_id,
            "event_id": uuid.uuid4().hex,
            **self._as_payload(message),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        # 事件日志只追加，不做原地覆盖，因此普通 append 即可，
        # 不需要 atomic_write 那套临时文件 + replace 的保护。
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
        # 计数在成功写入后 +1，保证 version_id 与落盘内容一一对应
        self._version_counters[path] = self._version_counters.get(path, 0) + 1
        return version_id

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _as_payload(message: Any) -> dict[str, Any]:
        if isinstance(message, dict):
            return message
        return {"payload": message}

    def _next_version_id(self, path: Path) -> str:
        """取下一个版本号；首次访问该文件时按已有行数预热。"""
        known = self._version_counters.get(path)
        if known is None:
            known = self._count_lines(path)
            self._version_counters[path] = known
        return str(known + 1)

    @staticmethod
    def _count_lines(path: Path) -> int:
        """按文件已有非空行数统计（仅每个会话文件首次写入时调用一次）。"""
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())