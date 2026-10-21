"""本地 runlog 导出器。

把遥测事件落到本地文件，作为远程收集端的**兜底**：
远程端点不可达时，事件仍然有地方可查。

**为什么需要它**：遥测端点在集群外时经常连不上（网络策略、证书、
端点维护）。没有本地兜底，这段时间的排障数据就彻底丢了 ——
而恰恰是「出问题时」最需要数据。

落盘格式用 JSON Lines：每行一个事件，追加写入。选它是因为
- 追加写不需要预分配文件，实现简单
- 崩溃时最多丢最后一行，不会像 JSON 那样整个文件不可解析
- 可用 ``jq`` 直接查

**历史缺口**：本模块此前是空文件，``event_sink`` 在
``_create_log_exporters`` 里无条件构造 ``RunlogLogExporter()``，
遥测初始化整体不可用。
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import threading
import typing

import opentelemetry.sdk._logs as sdk_logs
import opentelemetry.sdk._logs.export as log_export

logger = logging.getLogger(__name__)

#: 单个 runlog 文件的大小上限（字节），超过后轮转
_MAX_FILE_BYTES = 8 * 1024 * 1024
#: 保留的历史文件份数
_MAX_FILE_COUNT = 3


class RunlogLogExporter(log_export.LogRecordExporter):
    """把日志记录追加写入本地 JSON Lines 文件。"""

    def __init__(
        self,
        log_dir: pathlib.Path | str | None = None,
        *,
        file_name: str = "telemetry-runlog.jsonl",
    ) -> None:
        self._lock = threading.Lock()
        self._file_name = file_name
        self._log_dir = self._resolve_log_dir(log_dir)
        self._shutdown = False

    @staticmethod
    def _resolve_log_dir(log_dir: pathlib.Path | str | None) -> pathlib.Path:
        """定位落盘目录。

        优先用显式传入的目录；否则走工程统一的用户数据目录。
        目录创建失败时退回当前工作目录 —— 遥测不该因为写不了盘就崩掉主链路。
        """
        if log_dir is not None:
            directory = pathlib.Path(log_dir)
        else:
            try:
                from shared.paths import get_logs_dir

                directory = pathlib.Path(get_logs_dir())
            except Exception:  # noqa: BLE001 - 遥测是旁路，失败不阻断主流程
                directory = pathlib.Path.cwd() / "logs"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            directory = pathlib.Path.cwd()
        return directory

    @property
    def log_path(self) -> pathlib.Path:
        """当前 runlog 文件路径。"""
        return self._log_dir / self._file_name

    def export(self, batch: typing.Sequence[sdk_logs.ReadWriteLogRecord]) -> None:
        """把一批日志记录写入文件。"""
        if self._shutdown or not batch:
            return
        lines: list[str] = []
        for record in batch:
            payload = self._to_payload(record)
            if payload is None:
                continue
            try:
                lines.append(json.dumps(payload, ensure_ascii=False))
            except (TypeError, ValueError):
                continue
        if not lines:
            return

        with self._lock:
            try:
                self._rotate_if_needed()
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(lines) + "\n")
            except OSError as exc:
                logger.debug("runlog export failed: %s", exc)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """追加写无需缓冲，立即返回成功。"""
        return True

    def shutdown(self) -> None:
        """停止导出。"""
        self._shutdown = True

    # ------------------------------------------------------------ 内部

    def _to_payload(self, record: sdk_logs.ReadWriteLogRecord) -> dict[str, object] | None:
        """把一条日志记录转成可 JSON 序列化的字典。"""
        try:
            body = getattr(record, "body", None)
            attributes = getattr(record, "attributes", None) or {}
            return {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "body": body if isinstance(body, str) else repr(body),
                "severity": str(getattr(record, "severity_text", "") or ""),
                "attributes": {
                    str(key): str(value) for key, value in dict(attributes).items()
                },
            }
        except Exception:  # noqa: BLE001 - 单条记录序列化失败不应影响整批
            return None

    def _rotate_if_needed(self) -> None:
        """文件超过上限时轮转，保留有限份历史。"""
        path = self.log_path
        try:
            if not path.exists() or path.stat().st_size < _MAX_FILE_BYTES:
                return
        except OSError:
            return

        for index in range(_MAX_FILE_COUNT - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            target = path.with_name(f"{path.name}.{index + 1}")
            try:
                if source.exists():
                    os.replace(source, target)
            except OSError:
                continue
        try:
            os.replace(path, path.with_name(f"{path.name}.1"))
        except OSError:
            pass


__all__ = ["RunlogLogExporter"]
