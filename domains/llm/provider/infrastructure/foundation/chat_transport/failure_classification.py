"""provider 调用失败的分类策略。

``ProviderChatModelBase`` 以 ``failure_policy`` 的形式持有它，用来判断一次
后端错误是否值得重试、以及重试前该等多久。

分类口径刻意保守：只对明确的**可重试**信号（超时 / 限流 / 5xx / 连接错误）
返回 ``retryable=True``，其余一律 ``False``，避免把参数错误这类确定性失败
也卷进重试。
"""

from __future__ import annotations
import asyncio
import logging
import typing
import collections.abc as abc
import httpx
import pydantic

import domains.llm.provider.domain.provider_failure_markers as failure_markers
import domains.llm.provider.application.authentication.request_attestation as provider_request_attestation
from domains.llm.provider.domain import retry_policy
import domains.llm.provider.infrastructure.foundation.chat_transport.contracts as native_transport
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

_INPUT_TOOL_LARGE_MARKERS=(
    "context  length",
"    context_length_exceeded",
    "maximum context",
    "request too large",
    "payload too large",
    "too many tokens",
)

_MODEL_UNAVAILABLE_MARKERS=(
    "model_no_found",
    "model no found",
    "deployment no found",
    "unknown model",
)


JsonObject = dict[str, typing.Any]

logger = logging.getLogger(__name__)




class ProviderFailurePolicy:
     model_config = pydantic.ConfigDict(frozen=True, extra="forbid")
     permission_markers : tuple[str,...]=()
     quota_markers : tuple[str,...]=()
     request_rejected_markers : tuple[str,...]=()


class ProviderPermanentError(RuntimeError):
    """provider 调用的确定性失败（重试无意义）。

    形状对齐 ``ProviderCapabilityError``：``message`` + ``error_code`` +
    ``is_retryable``。消息是**脱敏后**的（只有状态码的通用含义或异常类型名），
    可以安全地一路抛到边界；原始异常挂在 ``__cause__`` 上，仅供服务端日志与
    调试查看，不进入面向用户的消息。
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "provider_call_failed",
        status_code: int | None = None,
        is_retryable: bool = False,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.is_retryable = is_retryable
        super().__init__(message)


def retry_cause(
    error: BaseException,
    *,
    is_stream: bool = False,
    retryable_hint: bool = False,
    policy: ProviderFailurePolicy | None = None,
) -> retry_policy.ProviderRetryCause | None:
    if permanent_failure(error,policy=policy) is not None:
        return None
    text =_combined_error_text(error)
    status = native_transport.provider_error_status(error)
    if status ==504:
        return retry_policy.ProviderRetryCause.GATEWAY_TIMEOUT
    if status ==429 or any(marker in text for marker in failure_markers.PROVIDER_FAILURE_RATE_LIMIT_ERROR_MARKERS):
        return retry_policy.ProviderRetryCause.RATE_LIMIT
    if status in {500, 502, 503,529} or any(
        marker in text for marker in failure_markers.PROVIDER_FAILURE_SERVICE_UNAVAILABLE_ERROR_MARKERS
    ):
        return retry_policy.ProviderRetryCause.SERVICE_UNAVAILABLE
    if isinstance(error, (httpx.TransportError,asyncio.TimeoutError)) or type(error).__name__=="APIConnectionError":
        return retry_policy.ProviderRetryCause.NETWORK_ERROR
    if any(marker in text for marker in failure_markers.PROVIDER_TRANSIENT_ERROR_MARKERS):
        return retry_policy.ProviderRetryCause.SERVICE_UNAVAILABLE
    if retryable_hint:
        return retry_policy.ProviderRetryCause.REQUEST_FAILED
    return None

def safe_permanent_error(
    error: BaseException ,
    *,
    policy: ProviderFailurePolicy | None = None,
) -> retry_policy.ProviderUserFacingError:
    faiure= permanent_failure(error,policy=policy) or retry_policy.ProviderRequestFailure.REQUEST_REJECTED
    raise retry_policy.ProviderUserFacingError(faiure)

def raise_safe_permanent_error(
    error: BaseException ,
    *,
    policy: ProviderFailurePolicy | None = None,
) -> typing.NoReturn:
    if isinstance(error, provider_request_attestation.ProviderRequestAttestationError) :
        raise error
    raise safe_permanent_error(error, policy=policy) from error

def permanent_failure(
    error: BaseException ,
    *,
    policy: ProviderFailurePolicy | None = None,
) -> retry_policy.ProviderRequestFailure|None:
    text=_combined_error_text(error)
    status = native_transport.provider_error_status(error)
    error_type=type(error).__name__
    if policy is not None:
        if any(marker.casefold() in text for marker in policy.quota_markers):
            return  retry_policy.ProviderRequestFailure.QUOTA_EXHAUSTED
        if any(marker.casefold() in text for marker in policy.permission_markers):
            return  retry_policy.ProviderRequestFailure.PERMISSION
        if any(marker.casefold() in text for marker in policy.request_rejected_markers):
            return  retry_policy.ProviderRequestFailure.REQUEST_REJECTED
    if status ==407 or "proxy authentication required" in text:
        return retry_policy.ProviderRequestFailure.PROXY_AUTHENTICATION
    if any(marker in text for marker in failure_markers.QUOTA_ERROR_MARKERS):
        return retry_policy.ProviderRequestFailure.QUOTA_EXHAUSTED
    if any(marker.casefold() in text for marker in failure_markers.MODEL_PERMISSION_ERROR_MARKERS):
        return retry_policy.ProviderRequestFailure.PERMISSION
    if any(marker in text for marker in failure_markers.PROVIDER_FAILURE_CAPABILITY_ERROR_MARKERS):
        return retry_policy.ProviderRequestFailure.UNSUPPORTED_CAPABILITY
    if any(marker in text for marker in _INPUT_TOOL_LARGE_MARKERS) or status==413:
        return retry_policy.ProviderRequestFailure.INPUT_TOOL_LARGE
    if any(marker in text for marker in _MODEL_UNAVAILABLE_MARKERS) or status==404:
        return retry_policy.ProviderRequestFailure.MODEL_UNAVAILABLE
    if status ==401 or "AuthorizationError" in error_type or "NotAuthenticatedError" in error_type:
        return retry_policy.ProviderRequestFailure.AUTHORIZATION
    if status ==403 or "PermissionDeiedError" in error_type :
        return retry_policy.ProviderRequestFailure.PERMISSION
    if status in{400, 405,409, 415, 422} or type(error).__name__=="BadRequestError":
        return retry_policy.ProviderRequestFailure.INVALID_REQUEST
    return None

@typing.runtime_checkable
class _HasError(typing.Protocol):
    error:object

@typing.runtime_checkable
class _HasMessage(typing.Protocol):
    message:object

@typing.runtime_checkable
class _HasCode(typing.Protocol):
    code:object

@typing.runtime_checkable
class _HasBody(typing.Protocol):
    body:object

def _combined_error_text(error: BaseException) -> str:
    values:list[object] = [str(error), *error.args]
    if isinstance(error,_HasBody):
       values.append(error.body)
    if isinstance(error,_HasMessage):
       values.append(error.message)
    if isinstance(error,_HasCode):
       values.append(error.code)
    if isinstance(error,_HasError):
       values.append(error.error)
    response = native_transport.provider_error_response(error)
    if response is not None:
        try:
            # list.extend() 只接受一个可迭代参数，这里要追加的是两个独立标量，
            # 必须逐个 append；原写法 values.extend(a, b) 会抛
            # "TypeError: list.extend() takes exactly one argument (2 given)"，
            # 反而把真正的 provider 报错（如 HTTP 400）盖住。
            values.append(response.status_code)
            values.append(response.text)
        except httpx.ResponseNotRead:
            values.append(response.status_code)
    return " ".join(text for value in values for text in _value_texts(value)).casefold()

def _value_texts(value:object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, bytes):
        return[value.decode("utf-8", errors="replace")]
    if isinstance(value, abc.Mapping):
        return [text for key, item in value.items() for text in (*_value_texts(key), *_value_texts(item))]
    if isinstance(value, abc.Iterable):
        return [text for item in value for text in _value_texts(item)]
    return [str(value)]





















