"""会话文件目录：按会话目录扫描并标准化会话元信息。

``SessionPersistence.list_sessions`` 的执行体在
:class:`SessionFileCatalogQuery` —— 它负责遍历会话目录、读取状态文件、
按 ``limit``/``offset`` 分页，并兼容旧格式（``_execution.json``）。

**已知缺口**（迁移遗留，非目录重组引入）：
``SessionFileCatalogQuery`` 与 ``JsonObject`` 在迁移中丢失定义，
导致 ``SessionPersistence.list_sessions`` 一调用就抛
``AttributeError: module has no attribute 'SessionFileCatalogQuery'``。
这里按 ``session_persistence.list_sessions`` 的调用契约补齐实现。
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import typing

import domains.configuration.domain.runtime_state.app_state as app_state_module
import domains.persistence.infrastructure.app_state_document as app_state_document

# 会话条目的标准形状：至少含 id / created_at / last_updated
JsonObject = typing.Dict[str, typing.Any]

# 会话状态文件名（新格式）
_APP_STATE_FILENAME = "app_state.json"
# 旧格式执行文件名（兼容用）
_EXECUTION_FILENAME = "_execution.json"

# 会话存储目录下的**非会话子目录**。
# 这些目录与会话同处一级，但装的是别的东西 —— 扫描时必须跳过，
# 否则会把内部的 UUID 子目录误当成会话 id。
_NON_SESSION_DIRS = frozenset({
    "agent_executions",   # Agent 执行记录：<session>/agents/<mode>/state.json
    "commands",
    "hooks",
    "logs",
    "skills",
    "plugins",
    "tmp",
    "cache",
})


def _is_session_id_like(name: str) -> bool:
    """判断目录名是否像会话 id（UUID 形式）。

    会话以 UUID 命名；非会话目录名固定为英文单词。
    这里用形状而非白名单判断，避免漏掉新增的内部目录。
    """
    parts = name.split("-")
    if len(parts) != 5:
        return False
    return all(p and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts)


# 历史上pydantic 的 repr 曾泄漏到会话文件名，形成
#   __value____<uuid>____has_result___True_.jsonl
# 这类损坏名。真实会话 id 仍嵌在中间，因此可以从文件名还原。
_CORRUPTED_NAME_PATTERN = re.compile(
    r"__value__+(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})_+"
)


def recover_session_id(name: str) -> str:
    """从（可能已损坏的）会话文件名还原真实 id。

    正常名直接返回；损坏名（``__value__`` 包裹）从中间提取 UUID。
    这样即使磁盘上留有历史脏数据，会话列表与恢复功能依然可用。
    """
    stem = name[: -len(".jsonl")] if name.endswith(".jsonl") else name
    matched = _CORRUPTED_NAME_PATTERN.match(stem)
    return matched.group("uuid") if matched else stem


def load_app_state_from_path(path: pathlib.Path) -> app_state_module.AppState:
    """从路径读取 AppState，兼容旧格式文档。"""
    document = app_state_document.normalize_legacy_document_json(
        path.read_text(encoding="utf-8")
    )
    return app_state_module.AppState.model_validate(document)


class SessionFileCatalogQuery:
    """会话目录查询。

    由 :meth:`SessionPersistence.list_sessions` 构造后调用 ``execute``。
    """

    def __init__(
        self,
        *,
        sessions_dir: pathlib.Path,
        search_dirs: typing.Sequence[pathlib.Path] = (),
        normalize_path: typing.Callable[[str], str] | None = None,
    ) -> None:
        self._sessions_dir = pathlib.Path(sessions_dir)
        self._search_dirs = list(search_dirs)
        self._normalize_path = normalize_path or (lambda value: value)

    def execute(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_old_format: bool = True,
        filter_tags: typing.Mapping[str, str | None] | None = None,
    ) -> list[JsonObject]:
        """扫描会话目录并返回标准化条目列表。"""
        rows: list[JsonObject] = []
        seen: set[str] = set()

        for search_dir in self._collect_search_dirs():
            if not search_dir.is_dir():
                continue
            for child in sorted(search_dir.iterdir()):
                row = self._read_session(child, include_old_format)
                if row is None:
                    continue
                session_id = str(row.get("id") or "")
                if not session_id or session_id in seen:
                    continue
                if filter_tags and not self._match_tags(row, filter_tags):
                    continue
                seen.add(session_id)
                rows.append(row)

        # 最近更新的排在前面
        rows.sort(key=lambda item: str(item.get("last_updated") or ""), reverse=True)

        start = max(offset, 0)
        end = start + limit if limit and limit > 0 else None
        return rows[start:end]

    # ------------------------------------------------------------ 内部

    def _collect_search_dirs(self) -> list[pathlib.Path]:
        dirs = [self._sessions_dir]
        for extra in self._search_dirs:
            candidate = pathlib.Path(extra)
            if candidate not in dirs:
                dirs.append(candidate)
        return dirs

    def _read_session(
        self, entry: pathlib.Path, include_old_format: bool
    ) -> JsonObject | None:
        """读取一个会话目录（或旧格式 .jsonl 文件）。"""
        if entry.is_file():
            if entry.suffix != ".jsonl":
                return None
            return self._row_from_jsonl(entry)

        if not entry.is_dir():
            return None
        # 跳过非会话目录（agent_executions 等）
        if entry.name.lower() in _NON_SESSION_DIRS or not _is_session_id_like(entry.name):
            return None

        app_state_path = entry / _APP_STATE_FILENAME
        has_app_state = app_state_path.is_file()
        has_execution = (entry / _EXECUTION_FILENAME).is_file()
        if not has_app_state and not (include_old_format and has_execution):
            return None

        row: JsonObject = {
            "id": recover_session_id(entry.name),
            "created_at": _iso(self._stat_time(entry, "created")),
            "last_updated": _iso(self._stat_time(entry, "modified")),
            "modified": _iso(self._stat_time(entry, "modified")),
            "working_directory": None,
            "topic": None,
            "tags": {},
            "size": self._dir_size(entry),
            "has_session_file": has_app_state,
            "has_execution_file": has_execution,
        }

        if has_app_state:
            raw = _read_json_object(app_state_path)
            if raw is not None:
                row.update(_row_from_state_document(raw, row))

        if row.get("working_directory") is None:
            row["working_directory"] = self._working_directory(entry)

        return row

    def _row_from_jsonl(self, path: pathlib.Path) -> JsonObject:
        stat = path.stat()
        return {
            "id": recover_session_id(path.name),
            "raw_file_name": path.name,
            "created_at": _iso(_from_timestamp(stat.st_ctime)),
            "last_updated": _iso(_from_timestamp(stat.st_mtime)),
            "modified": _iso(_from_timestamp(stat.st_mtime)),
            "working_directory": None,
            "topic": None,
            "tags": {},
            "size": stat.st_size,
            "has_session_file": True,
            "has_execution_file": False,
            "first_message": _first_message(path),
        }

    def _row_from_state_document(
        self, data: JsonObject, row: JsonObject
    ) -> JsonObject:
        """从 app_state.json 的原始字典提取标准化字段。

        注意：``AppState`` 目前是空模型（``class AppState(BaseModel): ...``），
        走 ``model_dump()`` 会得到 pydantic 内部序列化产物（``__value__`` 包裹），
        因此这里直接读原始 JSON。
        """
        if not isinstance(data, dict):
            return row
        session_id = data.get("session_id") or data.get("id") or row["id"]
        project_home = data.get("project_home") or data.get("working_directory")
        created = data.get("created_at") or data.get("created")
        updated = data.get("updated_at") or data.get("last_updated") or row["modified"]
        return {
            "id": session_id,
            "created_at": created or row["created_at"],
            "last_updated": updated or row["last_updated"],
            "modified": updated or row["modified"],
            "working_directory": self._normalize_path(project_home) if project_home else None,
            "topic": data.get("topic") or data.get("title"),
            "tags": dict(data.get("tags") or {}),
            "current_plan": data.get("current_plan"),
            "historrical_plans_conunt": data.get("historrical_plans_conunt")
            or len(data.get("historical_plans") or []),
        }

    def _working_directory(self, session_dir: pathlib.Path) -> str | None:
        """旧格式：从目录内的 _execution.json 读工作目录。"""
        execution = session_dir / _EXECUTION_FILENAME
        if not execution.is_file():
            return None
        try:
            payload = json.loads(execution.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        for key in ("project_home", "working_directory", "cwd", "project_dir"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return self._normalize_path(value)
        return None

    @staticmethod
    def _match_tags(row: JsonObject, filter_tags: typing.Mapping[str, str | None]) -> bool:
        tags = row.get("tags") or {}
        if not isinstance(tags, dict):
            return False
        for key, expected in filter_tags.items():
            actual = tags.get(key)
            if expected is None:
                if key in tags:
                    return False
                continue
            if str(actual) != str(expected):
                return False
        return True

    @staticmethod
    def _stat_time(entry: pathlib.Path, which: str) -> float:
        stat = entry.stat()
        return stat.st_ctime if which == "created" else stat.st_mtime

    @staticmethod
    def _dir_size(entry: pathlib.Path) -> int:
        total = 0
        for child in entry.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
        return total


def _read_json_object(path: pathlib.Path) -> JsonObject | None:
    """读取 JSON 对象；失败返回 None。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _from_timestamp(value: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(value)


def _iso(value: object) -> str | None:
    """把时间值规范成 ISO 字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return _from_timestamp(float(value)).isoformat()
    if isinstance(value, str):
        return value
    return None


def _first_message(path: pathlib.Path, max_chars: int = 200) -> str | None:
    """从 .jsonl 里取第一条用户消息作为预览。"""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                if message.get("role") not in (None, "user"):
                    continue
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()[:max_chars]
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return None


__all__ = ["JsonObject", "SessionFileCatalogQuery", "load_app_state_from_path"]