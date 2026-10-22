"""流式开关的解析策略。

一次调用要不要走 streaming，有三个来源，优先级从高到低：

1. 调用方显式传的 ``stream``（``create_model(..., stream=False)``）；
2. 用户级配置（``LLMProviderAdapterBase._load_user_config()`` 的产出）；
3. 兜底默认——开启。

只有显式给出的值才算数（``None`` 表示"没说"，继续往下一级问），这样
``stream=False`` 才能真的关掉流式，而不是被默认覆盖。
"""

from __future__ import annotations

import typing
import collections.abc
import collections.abc as abc

JsonObject = dict[str, typing.Any]

_STREAM_KEYS: tuple[str, ...] = ("stream", "streaming", "stream_enabled", "enable_streaming")
DEFAULT_CHUNK_IDLE_TIMEOUT=180
MIN_CHUNK_IDLE_TIMEOUT=10
MAX_CHUNK_IDLE_TIMEOUT=600

def _coerce_flag(value: object) -> bool | None:
    """把任意入参压成 ``True/False/None``（None = 没给出）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "y"}:
            return True
        if lowered in {"0", "false", "no", "off", "n", ""}:
            return False
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def resolve_streaming_enabled(
    requested: object = None,
    user_config: typing.Mapping[str, object] | None = None,
    *,
    default: bool = True,
) -> bool:
    """解析最终是否开启 streaming。"""
    explicit = _coerce_flag(requested)
    if explicit is not None:
        return explicit

    if isinstance(user_config, typing.Mapping):
        for key in _STREAM_KEYS:
            if key in user_config:
                from_config = _coerce_flag(user_config[key])
                if from_config is not None:
                    return from_config

    return default
def _clamp_chunk_idle_timeout(
    value:int
)->int:
    return max(MIN_CHUNK_IDLE_TIMEOUT, min(MAX_CHUNK_IDLE_TIMEOUT,value))

def resolve_chunk_idle_timeout(
    explicit_timeout: int |None,
    runtime_config: collections.abc.Mapping[str, object] | None,
)->int:
    if explicit_timeout is not None and explicit_timeout > 0:
        return _clamp_chunk_idle_timeout(int(explicit_timeout))
    if runtime_config:
        configured= runtime_config.get("chunkIdleTimeout")
        if isinstance(configured, int) and not isinstance(configured, bool) and configured >0:
            return _clamp_chunk_idle_timeout(configured)
    return DEFAULT_CHUNK_IDLE_TIMEOUT


__all__ = ["resolve_streaming_enabled"]
