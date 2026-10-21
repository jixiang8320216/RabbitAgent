"""OpenTelemetry 导出配置。

把应用层的遥测配置翻译成 OpenTelemetry 导出器需要的参数：
端点、协议、TLS 校验、部署环境。

**协议选择**：``HTTP`` 走 ``/v1/logs`` HTTP 接口，兼容性好、能穿过
只放行 HTTP 的网关；``GRPC`` 性能更好但需要网关支持 HTTP/2。

**TLS 校验默认关闭**：遥测端点常在集群内网（明文 HTTP），
对着自签证书强校验会让整条导出链路失败。校验开关因此可配。

**历史缺口**：本模块此前是空文件，``event_sink`` 依赖它的
``OpenTelemetryConfig`` / ``OtlpProtocol``，而
``composition`` 又依赖 ``TelemetryConfig.to_opentelemetry()`` 的产物。
"""

from __future__ import annotations

import enum
import os
import typing

import pydantic


class OtlpProtocol(enum.StrEnum):
    """OTLP 传输协议。"""

    HTTP = "http"
    GRPC = "grpc"


class OpenTelemetryConfig(pydantic.BaseModel):
    """OpenTelemetry 导出配置。"""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    #: OTLP 端点
    otlp_endpoint: str = "http://localhost:4318"
    #: 传输协议
    otlp_protocol: OtlpProtocol = OtlpProtocol.HTTP
    #: 是否校验 TLS 证书
    verify_tls: bool = False
    #: 部署环境标识（dev / staging / prod ...）
    deployment_environment: str = "unknown"
    #: 采集间隔（秒）
    export_interval_seconds: int = 5
    #: 批量导出上限
    export_batch_size: int = 100

    @classmethod
    def from_runtime_config(
        cls, config_data: typing.Mapping[str, typing.Any]
    ) -> "OpenTelemetryConfig":
        """从运行时配置构造。

        识别两种来源：嵌套的 ``telemetry.otlp`` 段，或平铺的
        ``otlpEndpoint`` / ``otlp_endpoint`` 等键。
        """
        data = dict(config_data or {})

        # 1) 嵌套段
        telemetry_section = data.get("telemetry")
        nested: dict[str, typing.Any] = {}
        if isinstance(telemetry_section, typing.Mapping):
            otlp_section = telemetry_section.get("otlp")
            if isinstance(otlp_section, typing.Mapping):
                nested = dict(otlp_section)

        def pick(*keys: str) -> typing.Any:
            for source in (nested, data):
                for key in keys:
                    if key in source and source[key] is not None:
                        return source[key]
            return None

        endpoint = pick("otlpEndpoint", "otlp_endpoint", "endpoint")
        protocol = pick("otlpProtocol", "otlp_protocol", "protocol")
        verify_tls = pick("verifyTls", "verify_tls")
        environment = pick("deploymentEnvironment", "deployment_environment", "environment")

        kwargs: dict[str, typing.Any] = {}
        if endpoint is not None:
            kwargs["otlp_endpoint"] = str(endpoint)
        if protocol is not None:
            kwargs["otlp_protocol"] = OtlpProtocol(str(protocol).lower())
        if verify_tls is not None:
            kwargs["verify_tls"] = bool(verify_tls)
        if environment is not None:
            kwargs["deployment_environment"] = str(environment)

        return cls(**kwargs)

    @classmethod
    def from_env(cls) -> "OpenTelemetryConfig":
        """从环境变量构造（``OTEL_EXPORTER_OTLP_*`` 约定）。"""
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        environment = os.environ.get("OTEL_DEPLOYMENT_ENVIRONMENT") or os.environ.get(
            "DEPLOYMENT_ENVIRONMENT"
        )
        kwargs: dict[str, typing.Any] = {}
        if endpoint:
            kwargs["otlp_endpoint"] = endpoint
        if protocol:
            try:
                kwargs["otlp_protocol"] = OtlpProtocol(protocol.lower())
            except ValueError:
                pass
        if environment:
            kwargs["deployment_environment"] = environment
        return cls(**kwargs)


__all__ = ["OpenTelemetryConfig", "OtlpProtocol"]
