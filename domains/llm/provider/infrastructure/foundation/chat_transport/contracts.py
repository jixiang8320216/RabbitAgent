"""provider 传输层契约（类型别名 + 钩子形状）。

只放**类型**，不放实现：``ProviderChatModelBase`` 的构造参数注解会在这里
取值，任何一个名字缺失都会让该模块导入失败。

- ``ProviderOptions``：透传给后端的生成参数（``temperature`` / ``max_tokens`` …）。
- ``AuthHook``：取凭证的钩子，形状对齐
  ``llm.provider.application.provider_authentication.ProviderAuthHook``。
- ``ModelIdentifyResolver``：把配置里的 model 名解析成后端认得的 id。
"""
import inspect
import typing
import collections.abc as abc
import asyncio
import httpx
import pydantic
import logging
import json


import domains.agent.domain.value_objects.chat_response as chat_contracts
import domains.agent.domain.value_objects.message as message_module
import domains.agent.domain.aggregates.chat_model as chat_model
logger = logging.getLogger(__name__)

JsonObject = dict[str, typing.Any]

type ProviderOptions = dict[str, typing.Any]


ChatResponse = chat_contracts.ChatResponse
ChatUsage = chat_contracts.ChatUsage
ProviderOptions = chat_model.JsonObject
ProviderMessage = chat_model.JsonObject

class AuthHookProjection(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid",strict=True)
    api_key: str|None=None
    headers: dict[str, str]= pydantic.Field(default_factory=dict)
    principal_environment:str|None=None
    account_fingerprint: str|None=None
    principal_revision:int|None=pydantic.Field(default=None,ge=1)

# 无参、异步、返回 ProviderAuthResult（api_key_id / headers / principal_*）
type AuthHook = abc.Callable[[], abc.Awaitable[object]]

# (model_name) -> 后端 model id
type ModelIdentifyResolver = abc.Callable[[str,str,str|None,int|None,str|None], abc.Awaitable[str]]

type AsyncProviderCall = abc.Callable[..., abc.Awaitable[object]]

class AsyncProviderClientPort(typing.Protocol):
    def close(self) -> abc.Awaitable[None]|None:...


class AsyncGeneratorClosePort(typing.Protocol):
     async def aclose(self) -> None:...

@typing.runtime_checkable
class ErrorResponseCarrier(typing.Protocol):

    response: httpx.Response


async def propagate_stream_cancellation(message:str)->None:
    cancelled: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    cancelled.cancel(message)
    await cancelled

async def close_provider_client(client: AsyncProviderClientPort,provider:str)->None:

    try:
        close_result=client.close()
        if inspect.isawaitable(close_result):
            await close_result
    except Exception as exc:
        logger.error(exc)

async def closer_async_generator(stream: AsyncGeneratorClosePort, provider:str):
    try:
        await stream.aclose()
    except Exception as exc:
        logger.error(exc)

async def close_provider_clients(clients: list[AsyncProviderClientPort], provider:str)->None:
    while clients:
        await close_provider_client(clients.pop(),provider)

async def close_latest_provider_client(clients: list[AsyncProviderClientPort], provider:str)->None:
    if clients:
        await close_provider_client(clients.pop(),provider)

def provider_error_response(error: BaseException) -> httpx.Response|None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response
    if isinstance(error, ErrorResponseCarrier) and isinstance(error.response, httpx.Response):
        return error.response
    return None

def provider_error_status(error:BaseException) -> int|None:
    response= provider_error_response(error)
    return response.status_code if response is not None else None

def tool_call_observer_projection(
        block: message_module.StreamContentBlock,
)->tuple[str,str,str]|None:
    if isinstance(block, message_module.ToolUseBlock):
        return block.id, block.name, json.dumps(block.input,ensure_ascii=False)[:500]
    if isinstance(block, message_module.ToolUseDeltaBlock) and block.id is not None and block.name is not None:
        return block.id, block.name, block.input_delta[:500]

def log_observer_callback_failure(callback:str, agent_name:str, error:Exception)->None:
    logger.debug("Callback failed callback =%s, agent=%s, error_type=%s", callback, agent_name, type(error).__name__)

def chat_response_has_business_output(response:chat_contracts.ChatResponse|None)->bool:
    if response is None or not isinstance(response.content, list):
        return False
    for block in response.content:
        if isinstance(block, message_module.TextBlock) and block.text:
            return True
        if isinstance(block, message_module.ThinkingBlock) and block.thinking:
            return True
        if isinstance(block, (message_module.ToolUseBlock, message_module.ToolUseDeltaBlock)) :
            return True
    return False

# 请求发出前的最后一道改写：(headers, payload) -> (headers, payload)
type RequestTransformer = abc.Callable[[JsonObject, JsonObject], tuple[JsonObject, JsonObject]]





__all__ = [
    "JsonObject",
    "ProviderOptions",
    "AuthHook",
    "ModelIdentifyResolver",
    "RequestTransformer",
]
