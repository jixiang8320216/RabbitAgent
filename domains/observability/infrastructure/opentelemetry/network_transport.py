"""OTLP 传输层的 TLS 处理。

OpenTelemetry 的 HTTP 导出器默认会校验 TLS 证书。但遥测端点通常部署在
集群内网、用自签证书或干脆是明文 HTTP —— 强校验会让整条导出链路失败。

这里提供两件事：

1. ``OTLPLogExporter`` —— 包装官方的 HTTP 导出器，按需关闭校验
2. ``apply_tls_verification`` —— 事后调整导出器的校验开关

**为什么两者都要**：官方导出器在构造时就把 session 建好了，
校验开关只能在 session 层改。因此先构造带宽松 session 的导出器，
再在必要时对 session 本身做修正。

**历史缺口**：本模块此前是空文件，``event_sink`` 调用
``OTLPLogExporter(verify_tls=...)`` 与 ``apply_tls_verification(...)`` 时
抛 AttributeError。
"""

from __future__ import annotations

import typing

import opentelemetry.exporter.otlp.proto.http._log_exporter as http_log_exporter

try:  # requests 在部分精简安装里缺失
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]


def _build_session(verify_tls: bool) -> typing.Any:
    """构造一个按需关闭校验的 HTTP session。"""
    if requests is None:
        return None
    session = requests.Session()
    session.verify = bool(verify_tls)
    return session


class OTLPLogExporter(http_log_exporter.OTLPLogExporter):
    """支持关闭 TLS 校验的 OTLP 日志导出器。

    官方导出器没有暴露校验开关，这里通过替换 session 实现。
    """

    def __init__(self, *, verify_tls: bool = True, **kwargs: typing.Any) -> None:
        session = _build_session(verify_tls)
        if session is not None:
            kwargs.setdefault("session", session)
        super().__init__(**kwargs)
        self._verify_tls = bool(verify_tls)


def apply_tls_verification(exporter: typing.Any, *, verify_tls: bool) -> None:
    """调整导出器的 TLS 校验开关。

    对已构造好的导出器补设校验策略 —— 官方导出器把 session 藏在
    ``_session`` 下，这里同时尝试公开属性与私有属性两条路径。
    """
    target = exporter
    session = getattr(target, "session", None)
    if session is None:
        session = getattr(target, "_session", None)
    if session is not None and hasattr(session, "verify"):
        session.verify = bool(verify_tls)
        return

    # 官方导出器把 session 包在 ``_session`` 委托对象里
    inner = getattr(target, "_session", None)
    if inner is not None:
        real = getattr(inner, "session", None)
        if real is not None and hasattr(real, "verify"):
            real.verify = bool(verify_tls)


__all__ = ["OTLPLogExporter", "apply_tls_verification"]
