"""provider 调用失败的重试策略共享定义。

这是 LLM provider 层的**共享**重试/失败契约，被以下模块共同依赖：

- ``.../foundation/chat_tranport/failure_classification``（失败分类）
- ``.../families/anthropic/request``（anthropic 传输）
- ``.../protocols/openai_compatible/request``（OpenAI 兼容传输）
- ``.../protocols/openai_compatible/responses_model``（Responses API 传输）

分类后的失败由 ``retry_coordinator`` 决定是否重试、重试多久；不可重试的失败会被包成
``ProviderUserFacingError`` 抛给上层，最终渲染给用户。
"""

from __future__ import annotations

import enum


class ProviderRetryCause(enum.Enum):
    """可重试的失败原因（交由 ``retry_coordinator`` 按退避策略重试）。"""

    GATEWAY_TIMEOUT = "gateway_timeout"
    RATE_LIMIT = "rate_limit"
    SERVICE_UNAVAILABLE = "service_unavailable"
    NETWORK_ERROR = "network_error"
    REQUEST_FAILED = "request_failed"
    FIRST_CHUNK_TIMEOUT = "first_chunk_timeout"
    EMPTY_RESPONSE = "empty_response"


class ProviderRequestFailure(str, enum.Enum):
    """确定性的（不可重试）请求失败原因。

    继承 ``str`` 以便直接作为用户可见的诊断码 / 文案使用。
    """

    REQUEST_REJECTED = "request_rejected"
    QUOTA_EXHAUSTED = "quota_exhausted"
    PERMISSION = "permission"
    PROXY_AUTHENTICATION = "proxy_authentication"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INPUT_TOOL_LARGE = "input_tool_large"
    MODEL_UNAVAILABLE = "model_unavailable"
    AUTHORIZATION = "authorization"
    INVALID_REQUEST = "invalid_request"

    def __str__(self) -> str:  # 让 str(成员) 直接等于诊断值
        return str(self.value)


class ProviderRetryExhaustedError(Exception):
    """重试次数耗尽后被抛出。"""


class ProviderRetryUserFacingError(Exception):
    """重试过程中判定为「应直接呈现给用户」的失败。"""


class ProviderUserFacingError(Exception):
    """不可重试的确定性失败，携带一个 ``ProviderRequestFailure`` 分类。

    ``failure_classification.safe_permanent_error`` 用它把后端异常收敛成
    可安全展示的用户态错误，避免内部细节外泄。
    """

    def __init__(self, failure: ProviderRequestFailure) -> None:
        super().__init__(failure)
        self.failure = failure

    @property
    def cause(self) -> str:
        return str(self.failure)


def provider_failure_diagnostic_code(error: BaseException) -> str:
    """把任意异常折算成一个稳定的诊断码字符串。

    用于 observer / telemetry 上报（``error=`` 字段），因此必须是**字符串**，
    且不能因为异常形态奇怪而再抛。
    """
    # 已经是我们自己的用户态错误 → 直接用其分类值
    if isinstance(error, ProviderUserFacingError):
        return str(error.failure)
    for attr in ("failure", "cause"):
        value = getattr(error, attr, None)
        if isinstance(value, ProviderRequestFailure):
            return str(value)
        if isinstance(value, str) and value:
            return value
    # 常见 HTTP 状态：优先用状态码做诊断码，稳定且可比
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return f"http_{status}"
    # 兜底：异常类名（截断过长内容）
    name = type(error).__name__
    if len(name) > 80:
        name = name[:80]
    return name or "unknown_error"
