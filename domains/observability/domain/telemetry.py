"""遥测领域模型：事件、严重级别与 sink 契约。

这一层只定义**形状**，不碰任何具体后端 —— OpenTelemetry、runlog 等
实现都在 infrastructure 层。应用层只依赖这里的协议。

**历史缺口**：本模块此前只有 ``TelemetrySeverity``，缺少
``TelemetryEvent`` 与 sink 协议，导致 ``event_sink`` 与 ``composition``
无法表达「发什么事件」「事件怎么送出去」。这里按调用契约补齐。
"""

from __future__ import annotations

import enum
import typing

import pydantic


class TelemetrySeverity(enum.StrEnum):
    """事件严重级别。"""

    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class TelemetryEvent(pydantic.BaseModel):
    """一条遥测事件。

    :param name: 事件名（如 ``agent.start`` / ``tool.execute``）
    :param severity: 严重级别
    :param attributes: 结构化属性，键值均为字符串
    """

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    name: str
    severity: TelemetrySeverity = TelemetrySeverity.INFO
    attributes: typing.Mapping[str, str] = pydantic.Field(default_factory=dict)

    def attribute_value(self, key: str) -> str | None:
        """取单个属性值。"""
        return self.attributes.get(key)


@typing.runtime_checkable
class EventSink(typing.Protocol):
    """事件落地端。

    遥测是旁路：sink 失败不应影响主链路，因此协议本身不声明异常语义。
    """

    def emit(self, event: TelemetryEvent) -> None:
        """发送一条事件。"""

    def shutdown(self) -> None:
        """关闭 sink，释放底层资源。"""


@typing.runtime_checkable
class MetricSink(typing.Protocol):
    """指标落地端。"""

    def record(
        self,
        name: str,
        value: float,
        *,
        attributes: typing.Mapping[str, str] | None = None,
    ) -> None:
        """记录一个指标点。"""

    def shutdown(self) -> None:
        """关闭 sink，释放底层资源。"""


__all__ = [
    "EventSink",
    "MetricSink",
    "TelemetryEvent",
    "TelemetrySeverity",
]
