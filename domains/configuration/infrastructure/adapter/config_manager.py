from __future__ import annotations

import json
import logging
import pathlib
import threading
from typing import Any

import pydantic

import domains.configuration.domain.protocol.config_protocol as config_protocol

logger = logging.getLogger(__name__)

_JSON_OBJECT_ADAPTER = pydantic.TypeAdapter(dict[str, pydantic.JsonValue])


class ConfigManager(config_protocol.ConfigManagerProtocol):
    """运行时配置管理器：以指定配置文件为正源，叠加会话级覆盖项。

    `config_file` 通常是 `build_runtime` 阶段 3 落盘的临时快照文件
    （`rabbit_harness_*.json`，见 `context/runtime_builder.py`），其中已包含
    package + user 分层合并结果；覆盖项优先级高于磁盘上的任何配置层。

    配置文件缺失、路径为空或解析失败时降级为「仅覆盖项」，
    不阻断运行时启动。
    """

    def __init__(
        self,
        config_file: str = "",
        *,
        # 兼容两种历史拼写：rabbit_application_composition 传 config_overrifes，
        # 其余新代码用 config_overrides；两者同时给出时以 config_overrides 为准。
        config_overrifes: dict[str, Any] | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.config_file = str(config_file) if config_file else ""
        if config_overrides is not None:
            overrides = config_overrides
        else:
            overrides = config_overrifes
        self._overrides: dict[str, Any] = dict(overrides or {})
        self._lock = threading.RLock()
        self._config_data: dict[str, Any] = {}
        self.reload_config()

    def _load_config(self) -> None:
        """从 config_file 读取配置；路径为空或读取失败时降级为空 dict。

        注意：空 config_file 不能交给 `Path("")`——它会变成当前目录 `.`，
        打开时抛 PermissionError（Windows 下的既有崩溃点）。
        """
        if not self.config_file:
            self._config_data = {}
            return
        config_path = pathlib.Path(self.config_file)
        if not config_path.is_file():
            logger.debug("config file not found: %s", config_path)
            self._config_data = {}
            return
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning("failed to read config file %s", config_path, exc_info=True)
            self._config_data = {}
            return
        if isinstance(payload, dict):
            self._config_data = _JSON_OBJECT_ADAPTER.validate_python(payload)
        else:
            self._config_data = {}

    def _merge_overrides(self, overrides: dict[str, Any]) -> None:
        """把覆盖项合并进当前配置：同为 dict 的键做深一层合并，其余整体替换。"""
        for key, value in overrides.items():
            current = self._config_data.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            else:
                self._config_data[key] = value

    def reload_config(self) -> None:
        """重新加载配置并重新应用覆盖项（协议要求的方法）。"""
        self._load_config()
        if self._overrides:
            self._merge_overrides(self._overrides)

    def get(self, key: str, default: Any = None) -> Any:
        """按 key 读取当前配置值；等价于 dict.get 的线程安全版本。"""
        with self._lock:
            return self._config_data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        """返回当前配置快照的浅拷贝，供调用方整体消费。"""
        with self._lock:
            return dict(self._config_data)

    def __contains__(self, key: str) -> bool:
        return key in self._config_data

    def __len__(self) -> int:
        return len(self._config_data)

    def get_property(self,
        key: str,
        default: pydantic.JsonValue = None,
        expected_type: type[object] |None=None,
        )->pydantic.JsonValue:
        keys=key.split(".")
        value = self._config_data
        try:
            for k in keys:
                value = value[k]
        except (KeyError, TypeError):
            return default

        if expected_type and value is not None:
            try:
                if expected_type is bool:
                    if isinstance(value, str):
                        return value.lower()  in ("yes", "true", "t", "1")
                    return bool(value)
                elif expected_type is int:
                    return int(value)
                elif expected_type is float:
                    return float(value)
                elif expected_type is list:
                    return list(value) if isinstance(value, (list,tuple)) else [value]
                elif expected_type is dict:
                    return dict(value) if isinstance(value, dict) else {}
                else:
                    return expected_type(value)
            except (KeyError, TypeError):
                return value
        return value
