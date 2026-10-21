import typing
import collections.abc as abc

import domains.llm.provider.infrastructure.families.anthropic.contracts as native_transport
import domains.agent.domain.aggregates.runtime_observer as runtime_observer
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.value_objects.chat_response as chat_contracts

InterruptibleSleep = abc.Callable[[float, str],abc.Awaitable[None]]

class AnthropicMessagesPort(typing.Protocol):
    create: native_transport._AsyncProviderCall
    stream: native_transport._AnthropicStreamCall


class AnthropicBetaPort(typing.Protocol):
    messages: AnthropicMessagesPort

class AnthropicClientPort(native_transport._AsyncProviderClientPort,typing.Protocol):
    beta: AnthropicBetaPort

class AnthropicRequestModel(typing.Protocol):
    model_name:str
    agent_name:str
    stream: bool
    observer: runtime_observer.RuntimeObserver
    max_retries_on_empty:int
    chunk_idle_timeout:float
    _init_generate_kwargs: chat_model.JsonObject
    model_kwargs: chat_model.JsonObject

    def _create_anthropic_client(
        self,
        request_headers: dict[str, str]|None=None,
    )->AnthropicClientPort:...

    async def _prepare_provider_attempt(self, attempt_number:int)->tuple[str,dict[str, str]]:...

    def _get_provider_name(self)->str:...

    def _cc_device_id(self)->str:...

    def _detect_cache_break(self, input_tokens: int, cache_read: int) -> None:...

    @staticmethod
    def _split_messages(
        messages: list[chat_model.JsonObject],
    )->tuple[str|None, list[native_transport.ProviderMessage]]:...

    @classmethod
    def _mark_last_message_cacheable(
        cls,
        api_messages: list[native_transport.ProviderMessage],
    )->None:...

    @classmethod
    def _split_system_into_blocks(
        cls,
        system_text:str|None,
    )-> list[native_transport.ProviderMessage]:...

    @staticmethod
    def _stabilize_tool_order(tools: list[native_transport.ProviderMessage]|None)->list[native_transport.ProviderMessage]|None:...

    @staticmethod
    def _convert_tools(tools: list[chat_model.JsonObject])->list[native_transport.ProviderMessage]:...

    @staticmethod
    def _anthropic_event_to_chat_response(event:object)->native_transport.ChatResponse|None:...

    @staticmethod
    def _anthropic_message_to_chat_response(raw_message:object)->native_transport.ChatResponse:...

    async def _send_thinking_start(
        self,
        operation_id:str,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject]|None,
        operation_kind: str|None=None,
       )->None:...

    async def _send_thinking_end(
        self,
        operation_id:str,
        input_tokens:int,
        output_tokens:int,
        reasoning_tokens:int,
        tool_call_ids:list[str],
        reasoning_content:str|None=None,
        cached_tokens:int=0,
        model_id: str|None=None,
        operation_kind:str|None=None,
    )->None:...

    def _record_tokens(
        self,
        input_tokens:int,
        output_tokens:int,
        reasoning_tokens:int=0,
        cached_tokens:int=0,
        *,
        operation_kind: str="normal",
        )->None:...

    async def _apply_chat_params_hook(self, kwargs: chat_model.JsonObject)->chat_model.JsonObject:...

ResponseGenerator = typing.AsyncGenerator[chat_contracts.ChatResponse, None]