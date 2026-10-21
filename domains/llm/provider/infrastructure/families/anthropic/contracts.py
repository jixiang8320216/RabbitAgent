"""Anthropic family 的**结构契约**层。

这里只放两类东西：

1. ``typing.Protocol`` —— 描述 Anthropic SDK 对象的**形状**，用来做注解和
   ``isinstance`` 鸭子判定。SDK 装在不在、装的是哪个版本都不影响本模块
   导入：全部是结构化类型，不依赖 ``anthropic`` 包。
2. 一小撮与流生命周期有关的**释放工具**（``_exit_anthropic_stream_manager``）。

为什么单独一层：``request.py`` / ``request_contracts.py`` / ``chat_model.py``
都要引用这些名字，而它们之间不能互相 import（会成环）。契约层**不 import
family 内任何模块**，也不 import ``runtime.model.*``（那条链上的
``message.py`` 目前会让 pydantic 生成 schema 失败），因此本模块是整条
import 链上最底下的那一块，永远能先起来。

命名口径：带下划线前缀 = family 内部契约，不对外暴露。
"""

from __future__ import annotations

import logging
import types
import typing

import pydantic

import domains.llm.provider.infrastructure.foundation.chat_transport.contracts as shared_contracts
import domains.agent.domain.aggregates.chat_model as chat_model
logger = logging.getLogger(__name__)

__all__ = [
    "_StopDetailProjection",
    "_AnthropicContentBlockProjection",
    "_AnthropicDeltaProjection",
    "_AnthropicMessageProjection",
    "_AnthropicEventProjection",
    "_AnthropicEventStreamPort",
    "_AnthropicStreamManagerPort",
    "_exit_anthropic_stream_manager",
]

ChatResponse = shared_contracts.ChatResponse
ProviderMessage = shared_contracts.ProviderMessage
_AsyncProviderCall = shared_contracts.AsyncProviderCall
_AsyncProviderClientPort = shared_contracts.AsyncProviderClientPort

# ----------------------------------------------------------------------
# 内容侧的形状
# ----------------------------------------------------------------------


class _StopDetailProjection(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore", from_attributes=True, strict=True)
    type: str | None=None
    category: str |None=None
    explanation: str |None=None


class _AnthropicContentBlockProjection(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore", from_attributes=True, strict=True)

    type: str
    text: str|None=None
    thinking: str|None=None
    id: str|None=None
    name: str|None=None
    input: chat_model.JsonObject|None=None


class _AnthropicDeltaProjection(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore", from_attributes=True, strict=True)

    type: str |None=None
    text: str|None=None
    thinking: str|None=None
    partial_json: str|None=None
    stop_reason: str |None=None
    stop_details: str |None=None


class _AnthropicMessageProjection(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore", from_attributes=True, strict=True)

    content: list[_AnthropicContentBlockProjection]=pydantic.Field(default_factory=list)
    stop_reason: str | None=None
    stop_details: object | None=None
    usage: object | None=None


class _AnthropicEventProjection(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore", from_attributes=True, strict=True)

    type: str
    index: int | None=None
    message: _AnthropicMessageProjection | None=None
    content_block: _AnthropicContentBlockProjection | None=None
    delta: _AnthropicDeltaProjection | None=None
    usage: object | None=None


# ----------------------------------------------------------------------
# 流 / 管理器端口
# ----------------------------------------------------------------------


@typing.runtime_checkable
class _AnthropicEventStreamPort(typing.Protocol):
    def __aiter__(self) -> _AnthropicEventProjection: ...

    async def __anext__(self) -> object: ...


@typing.runtime_checkable
class _AnthropicStreamManagerPort(typing.Protocol):

    async def __aenter__(self) -> _AnthropicEventStreamPort: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> bool|None: ...



async def _exit_anthropic_stream_manager(
    manager: _AnthropicStreamManagerPort ,
    provider_name: str,
) -> None:
     try:
        await manager.__aexit__(None, None, None)
     except BaseException as exc:
        logger.error(exc)
        return

_chat_response_has_business_output= shared_contracts.chat_response_has_business_output
_closer_latest_provider_client = shared_contracts.close_latest_provider_client
_close_provider_client = shared_contracts.close_provider_client
_close_provider_clients= shared_contracts.close_provider_clients
_log_observer_callback_failure = shared_contracts.log_observer_callback_failure
_Propagate_stream_cancellation = shared_contracts.propagate_stream_cancellation
_provider_error_response = shared_contracts.provider_error_response
_provider_error_status= shared_contracts.provider_error_status
_tool_call_observer_projection= shared_contracts.tool_call_observer_projection