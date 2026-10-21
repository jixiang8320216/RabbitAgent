"""遥测配置。

应用的遥测开关在运行时配置里（``telemetry.*`` 段），本模块把那份
「给业务看的配置」翻译成两个各自领域的形式：

- :meth:`TelemetryConfig.to_policy` → 领域策略（是否采集、是否记录内容、
  字符串截断长度），供应用层判断「这条数据要不要记」
- :meth:`TelemetryConfig.to_opentelemetry` → OpenTelemetry 导出配置

为什么要分两路：策略管「采不采」，导出配置管「怎么送」。
两者关心的参数不同，硬塞进一个对象会让「关掉遥测」变成要改一堆导出参数。

**历史缺口**：本模块此前是空壳（``class TelemetryConfig: pass``），
``composition`` 调用 ``from_runtime_config`` / ``to_policy`` / ``to_opentelemetry``
时抛 AttributeError，遥测初始化整体不可用。
"""

from __future__ import annotations

import typing

import pydantic

import domains.observability.domain.policy as policy
import domains.observability.infrastructure.opentelemetry.otlp_export_config as otlp_config


class TelemetryConfig(pydantic.BaseModel):
    """遥测配置。"""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    #: 总开关
    enabled: bool = True
    #: 是否记录内容（prompt / 响应正文）
    capture_content: bool = True
    #: 单个字符串属性的最大长度
    max_string_len: int = pydantic.Field(default=4096, gt=0)

    #: OTLP 导出配置
    otlp: otlp_config.OpenTelemetryConfig = pydantic.Field(
        default_factory=otlp_config.OpenTelemetryConfig
    )

    @classmethod
    def from_runtime_config(
        cls, config_data: typing.Mapping[str, typing.Any] | None
    ) -> "TelemetryConfig":
        """从运行时配置构造。

        识别 ``telemetry.*`` 段下的 ``enabled`` / ``captureContent`` /
        ``maxStringLen`` / ``otlp.*``。
        """
        data = dict(config_data or {})
        section = data.get("telemetry")
        telemetry_section: dict[str, typing.Any] = {}
        if isinstance(section, typing.Mapping):
            telemetry_section = dict(section)

        def pick(*keys: str) -> typing.Any:
            for key in keys:
                if key in telemetry_section and telemetry_section[key] is not None:
                    return telemetry_section[key]
            return None

        kwargs: dict[str, typing.Any] = {}

        enabled = pick("enabled")
        if enabled is not None:
            kwargs["enabled"] = bool(enabled)

        capture_content = pick("captureContent", "capture_content")
        if capture_content is not None:
            kwargs["capture_content"] = bool(capture_content)

        max_string_len = pick("maxStringLen", "max_string_len")
        if max_string_len is not None:
            kwargs["max_string_len"] = int(max_string_len)

        # OTLP 子配置：把整个 telemetry 段传下去，由它自己找 otlp 段
        kwargs["otlp"] = otlp_config.OpenTelemetryConfig.from_runtime_config(
            {"telemetry": telemetry_section}
        )

        return cls(**kwargs)

    def to_policy(self) -> policy.ObservabilityPolicy:
        """转成领域策略。"""
        return policy.ObservabilityPolicy(
            enabled=self.enabled,
            capture_content=self.capture_content,
            max_string_len=self.max_string_len,
        )

    def to_opentelemetry(self) -> otlp_config.OpenTelemetryConfig:
        """转成 OpenTelemetry 导出配置。"""
        return self.otlp


__all__ = ["TelemetryConfig"]
