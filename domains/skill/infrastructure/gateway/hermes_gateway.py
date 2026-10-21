

"""Durable gateway used by the Hermes skill-evolution workflow."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# 临时AI生成，待补齐
class HermesGatewayService:
    """Persist workflow state, events, and resumable sessions locally."""

    def __init__(self, *, durable_root: Path | None = None) -> None:
        self.durable_root = Path(durable_root or (Path.home() / ".rabbit-agent" / "hermes"))
        self.sessions_root = self.durable_root / "sessions"
        self.state_file = self.durable_root / "state.json"
        self.events_file = self.durable_root / "events.jsonl"
        self._lock = threading.RLock()

        self.sessions_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_identifier(identifier: str) -> str:
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("identifier must be a non-empty string")
        if Path(identifier).name != identifier or identifier in {".", ".."}:
            raise ValueError("identifier must be a plain file name")
        return identifier

    def _write_json(self, path: Path, value: Any) -> None:
        temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def read_state(self) -> dict[str, Any]:
        with self._lock:
            if not self.state_file.exists():
                return {}
            with self.state_file.open("r", encoding="utf-8") as stream:
                state = json.load(stream)
            if not isinstance(state, dict):
                raise ValueError("Hermes state must be a JSON object")
            return state

    def update_state(self, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise TypeError("values must be a mapping")
        with self._lock:
            state = self.read_state()
            state.update(dict(values))
            self._write_json(self.state_file, state)
            return state

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")
        record = {"timestamp": self._timestamp(), **dict(event)}
        with self._lock:
            with self.events_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return record

    def iter_events(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            if not self.events_file.exists():
                return
            with self.events_file.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise ValueError("Hermes event must be a JSON object")
                        yield record

    def save_session(self, session_id: str, session: Mapping[str, Any]) -> dict[str, Any]:
        session_id = self._validate_identifier(session_id)
        if not isinstance(session, Mapping):
            raise TypeError("session must be a mapping")
        value = {"session_id": session_id, **dict(session), "updated_at": self._timestamp()}
        with self._lock:
            self._write_json(self.sessions_root / f"{session_id}.json", value)
        return value

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        session_id = self._validate_identifier(session_id)
        path = self.sessions_root / f"{session_id}.json"
        with self._lock:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as stream:
                session = json.load(stream)
            if not isinstance(session, dict):
                raise ValueError("Hermes session must be a JSON object")
            return session

    def list_sessions(self) -> list[str]:
        with self._lock:
            return sorted(path.stem for path in self.sessions_root.glob("*.json"))






