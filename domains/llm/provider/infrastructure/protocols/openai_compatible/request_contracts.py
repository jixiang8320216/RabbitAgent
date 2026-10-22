
import typing
import logging
import collections.abc
import collections.abc as abc

import domains.llm.provider.infrastructure.protocols.openai_compatible.projections as native_transport
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.runtime_observer as runtime_observer
import domains.agent.domain.value_objects.message as message_module
import domains.agent.domain.value_objects.chat_response as chat_contracts

#: 可中断睡眠：接收 (秒数, 原因)，返回可等待对象。
#: 与 anthropic 版 request_contracts 保持同一签名，便于两条 provider 链路
#: 复用同一套中断与重试编排。
InterruptibleSleep = collections.abc.Callable[[float, str], abc.Awaitable[None]]


class OpenAICompletionPort(typing.Protocol):
    create: native_transport._AsyncProviderCall

class OpenAIChatPort(typing.Protocol):
    completions: OpenAICompletionPort

class OpenAIClientPort(native_transport._AsyncProviderClientPort, typing.Protocol):
    chat: OpenAIChatPort

class OpenAIRequestModel(typing.Protocol):
    model_name:str
    agent_name:str
    stream:bool
    observer: runtime_observer.RuntimeObserver
    max_retries_on_empty:int
    chunk_idle_timeout:float
    _auth_hook: native_transport.AuthHook|None
    _model_identity_resolver: native_transport.ModelIdentityResolver|None
    _auth_api_key: str|None
    _auth_header: dict[str,str]
    _default_headers: dict[str,str]
    _init_generate_kwargs: chat_model.JsonObject
    model_kwargs: chat_model.JsonObject

    def _create_openai_client(
        self,
        request_headers: dict[str, str]|None=None,
    )->OpenAIClientPort:...

    def _get_provider_name(self)->str:...

    def _is_deepseek_reasoning_content_error(self, error:BaseException)->bool:...





ResponseGenerator = typing.AsyncGenerator[chat_contracts.ChatResponse,None]
StreamBlock= message_module.StreamContentBlock
RequestLogger= logging.Logger































