"""遥测 sink 的运行时注册表。

进程内当前生效的事件/指标 sink 存在这里。业务代码只管
``telemetry_runtime.current_event_sink()`` 拿 sink 去发事件，
不关心背后是 OpenTelemetry 还是空实现。

为什么要注册表：遥测是**可切换**的。初始化前用空 sink（不产生任何开销），
初始化后换成真实 sink，关闭时再换回空的。业务代码全程不用改。

**历史缺口**：本模块此前是空文件，``composition`` 调用
``telemetry_runtime.NullEventSink()`` / ``replace_sinks(...)`` / ``reset_sinks()``
时抛 AttributeError，遥测初始化路径整体不可用。
"""

from __future__ import annotations

import threading
import typing

import domains.observability.domain.telemetry as telemetry


class NullEventSink:
    """空事件 sink：什么都不做。

    用于遥测未启用或已关闭的场景。相比「返回 None 让调用方判空」，
    提供一个真实对象可以让调用方无需分支判断。
    """

    def emit(self, event: telemetry.TelemetryEvent) -> None:
        """丢弃事件。"""

    def shutdown(self) -> None:
        """无资源可释放。"""


class NullMetricSink:
    """空指标 sink：什么都不做。"""

    def record(
        self,
        name: str,
        value: float,
        *,
        attributes: typing.Mapping[str, str] | None = None,
    ) -> None:
        """丢弃指标点。"""

    def shutdown(self) -> None:
        """无资源可释放。"""


class _RuntimeState:
    """当前生效的 sink。"""

    def __init__(self) -> None:
        self.event_sink: telemetry.EventSink = NullEventSink()
        self.metric_sink: telemetry.MetricSink = NullMetricSink()


_state = _RuntimeState()
_lock = threading.Lock()


def current_event_sink() -> telemetry.EventSink:
    """取当前生效的事件 sink。"""
    with _lock:
        return _state.event_sink


def current_metric_sink() -> telemetry.MetricSink:
    """取当前生效的指标 sink。"""
    with _lock:
        return _state.metric_sink


def replace_sinks(
    *,
    event_sink: telemetry.EventSink | None = None,
    metric_sink: telemetry.MetricSink | None = None,
) -> tuple[telemetry.EventSink, telemetry.MetricSink]:
    """替换 sink，返回被换下的旧 sink 供调用方关闭。

    :param event_sink: 新的事件 sink；``None`` 表示沿用当前
    :param metric_sink: 新的指标 sink；``None`` 表示沿用当前
    :return: (旧事件 sink, 旧指标 sink)
    """
    with _lock:
        previous = (_state.event_sink, _state.metric_sink)
        if event_sink is not None:
            _state.event_sink = event_sink
        if metric_sink is not None:
            _state.metric_sink = metric_sink
        return previous


def reset_sinks() -> tuple[telemetry.EventSink, telemetry.MetricSink]:
    """把 sink 换回空实现，返回被换下的旧 sink 供调用方关闭。"""
    with _lock:
        previous = (_state.event_sink, _state.metric_sink)
        _state.event_sink = NullEventSink()
        _state.metric_sink = NullMetricSink()
        return previous


__all__ = [
    "NullEventSink",
    "NullMetricSink",
    "current_event_sink",
    "current_metric_sink",
    "replace_sinks",
    "reset_sinks",
]
