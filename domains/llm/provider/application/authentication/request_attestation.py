"""请求出证（request attestation）。

某些 provider（企业网关 / 带签名的中转）要求每次请求都带一份"凭证快照"：
不是静态 api key，而是一次性的签名头。`ProviderChatModelBase` 通过
可选的 ``request_attestor`` 持有它，在真正发请求前取一次凭证。

这里只定义协议（``Protocol``），具体实现由启用该能力的 provider 提供；
不传即表示该 provider 不需要出证。
"""

from __future__ import annotations

import typing

JsonObject = dict[str, typing.Any]


@typing.runtime_checkable
class ProviderRequestAttestorProtocol(typing.Protocol):
    """请求出证器协议。

    ``attest`` 返回要合并进请求头的字段（例如 ``{"Authorization": ...}``）；
    实现可以是同步也可以是异步（``await`` 一个非 awaitable 的 dict 是调用方的
    责任，这里不做约束）。
    """

    def attest(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        payload: JsonObject | None = None,
    ) -> JsonObject | typing.Awaitable[JsonObject]:
        ...


class NoOpRequestAttestor:
    """缺省实现：什么也不加。"""

    def attest(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        payload: JsonObject | None = None,
    ) -> JsonObject:
        return {}


class ProviderRequestAttestationError(Exception):
    """请求出证失败。

    当 provider 要求一次性签名凭证、而签名获取失败（未配置网关凭据 / 签名服务不可用 /
    返回的凭证字段残缺）时抛出。

    它被 ``failure_classification.raise_safe_permanent_error`` 特判：不做重试、
    也不折叠成通用错误，而是**原样冒泡**给上层。理由是出证失败通常意味着配置缺失，
    重试不会改变结果，应把原始错误信息呈现给使用者。
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model_name = model_name


__all__ = [
    "ProviderRequestAttestorProtocol",
    "NoOpRequestAttestor",
    "ProviderRequestAttestationError",
]
