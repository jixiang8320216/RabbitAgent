"""Agent spawn 清单（manifest）仓库。

与 :mod:`agent.infrastructure.fiel_agent_execution_store` 的分工：

- **execution store** 记录「一个 Agent 执行到哪一步」，是随执行不断更新的状态；
- **manifest repository** 记录「一次 spawn 计划包含哪些 Agent、各自配置如何」，
  是 spawn 时刻的不可变快照。

恢复流程需要两者配合：先用 manifest 还原「应该存在哪些 Agent」，再用
execution store 判断「各自执行到什么程度」，从而决定续跑还是重跑。

文件名沿用模块名中的既定 typo（``mainfest`` 应为 ``manifest``），保持与导入点
一致；类名使用同样的拼写以匹配调用点 ``FileAgentSpawnMainfestRepository``。
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import typing
import uuid

logger = logging.getLogger(__name__)

__all__ = ["FileAgentSpawnMainfestRepository"]


class FileAgentSpawnMainfestRepository:
    """以 JSON 文件保存 spawn 清单。

    清单落盘在 ``<execution_store.base_dir>/manifests/`` 下，一次 spawn 一个
    文件，文件名由 ``spawn_id`` 决定。复用 execution store 的 base_dir 是为了
    让「同一个 Agent 的执行状态与其 spawn 清单」物理上聚合在一起，便于排查与
    整体清理。
    """

    _MANIFEST_DIR_NAME = "manifests"

    def __init__(self, execution_store: typing.Any = None, base_dir: str | pathlib.Path | None = None) -> None:
        """构造仓库。

        :param execution_store: execution store 实例；用于推导默认落盘目录。
        :param base_dir: 显式指定清单目录；``None`` 时从 ``execution_store`` 推导。
        """
        self._execution_store = execution_store
        resolved = base_dir if base_dir is not None else self._derive_base_dir(execution_store)
        self.base_dir = pathlib.Path(resolved).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    @staticmethod
    def _derive_base_dir(execution_store: typing.Any) -> pathlib.Path:
        """从 execution store 推导清单目录；无法推导时退到临时目录。"""
        store_base = getattr(execution_store, "base_dir", None)
        if store_base:
            return pathlib.Path(store_base) / FileAgentSpawnMainfestRepository._MANIFEST_DIR_NAME
        import tempfile

        fallback = pathlib.Path(tempfile.gettempdir()) / "rabbit-agent-spawn-manifests"
        logger.warning("spawn manifest repository falls back to temporary directory: %s", fallback)
        return fallback

    def _manifest_path(self, spawn_id: str) -> pathlib.Path:
        return self.base_dir / f"{spawn_id}.json"

    def save(
        self,
        manifest: typing.Mapping[str, typing.Any] | None = None,
        *,
        spawn_id: str | None = None,
    ) -> str | None:
        """保存一份 spawn 清单；返回实际使用的 ``spawn_id``（失败返回 ``None``）。"""
        identifier = str(spawn_id or (manifest or {}).get("spawn_id") or uuid.uuid4().hex)
        payload = dict(manifest or {})
        payload.setdefault("spawn_id", identifier)
        try:
            with self._lock:
                self.base_dir.mkdir(parents=True, exist_ok=True)
                target = self._manifest_path(identifier)
                tmp_path = target.with_suffix(".json.tmp")
                tmp_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp_path.replace(target)
            return identifier
        except OSError:
            logger.warning("failed to persist spawn manifest %s", identifier, exc_info=True)
            return None

    def load(self, spawn_id: str) -> dict[str, typing.Any] | None:
        """读取一份 spawn 清单；缺失或损坏返回 ``None``。"""
        target = self._manifest_path(str(spawn_id))
        if not target.is_file():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("failed to read spawn manifest %s", spawn_id, exc_info=True)
            return None
        return payload if isinstance(payload, dict) else None

    def list_spawn_ids(self) -> list[str]:
        """列出全部已保存的 ``spawn_id``。"""
        try:
            return sorted(entry.stem for entry in self.base_dir.glob("*.json"))
        except OSError:
            return []

    def latest(self) -> dict[str, typing.Any] | None:
        """返回最近修改的一份清单；没有任何清单时返回 ``None``。"""
        try:
            candidates = [entry for entry in self.base_dir.glob("*.json") if entry.is_file()]
        except OSError:
            return None
        if not candidates:
            return None
        newest = max(candidates, key=lambda entry: entry.stat().st_mtime)
        return self.load(newest.stem)

    def delete(self, spawn_id: str) -> bool:
        """删除一份清单；返回是否确实删除。"""
        try:
            self._manifest_path(str(spawn_id)).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            logger.warning("failed to delete spawn manifest %s", spawn_id, exc_info=True)
            return False

    def __repr__(self) -> str:
        return f"FileAgentSpawnMainfestRepository(base_dir={str(self.base_dir)!r})"
