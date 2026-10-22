from __future__ import annotations

import abc
import typing

import domains.llm.provider.application.formatting.message_formatters as message_formatters
import domains.llm.provider.application.formatting.compatible_thinking as openai_compatible_thinking
import domains.llm.provider.application.formatting.chat_formatters as provider_chat_formatters
import domains.llm.provider.application.formatting.responses_formatter as openai_responses_formatter
from domains.llm.provider.domain import openai_api_protocol
from domains.llm.provider.infrastructure.protocols.openai_compatible import retry_policy
import domains.llm.provider.infrastructure.protocols.openai_compatible.responses_model as openai_responses_model

def resolve_openai_protocol_strategy(
    protocol: openai_api_protocol.OpenAIAPIProtocol,
    *,
    chat_completions_model_type: type[retry_policy.NativeOpenAIChatModel] = retry_policy.NativeOpenAIChatModel,
    deepseek_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
        provider_chat_formatters.DeepSeekReasoningFormatter
    ),
    string_content_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
        provider_chat_formatters.StringContentOpenAIFormatter
    ),
    kimi_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
        provider_chat_formatters.KimitChatFormatter
    ),
    default_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
        provider_chat_formatters.TruncatingOpenAIChatFormatter
    ),
) -> OpenAIProtocolStrategy:
    if protocol == openai_api_protocol.OpenAIAPIProtocol.RESPONSES:
        return _RESPONSE_STRATEGY
    return ChatCompletionsProtocolStrategy(
        model_type=chat_completions_model_type,
        deepseek_formatter=deepseek_formatter,
        string_content_formatter=string_content_formatter,
        kimi_formatter=kimi_formatter,
        default_formatter=default_formatter,
    )

class OpenAIProtocolStrategy(abc.ABC):

    @property
    @abc.abstractmethod
    def model_type(self) -> type[retry_policy.NativeOpenAiChatModel]: ...

    @property
    @abc.abstractmethod
    def model_label(self) -> str: ...

    @abc.abstractmethod
    def create_formatter(self, *, model_name: object, tool_args_compression_limit: int) -> message_formatters.MessageFormatter: ...


class ChatCompletionsProtocolStrategy(OpenAIProtocolStrategy):

    def __init__(
        self,
        model_type: type[retry_policy] = retry_policy.NativeOpenAIChatModel,
        deepseek_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
            provider_chat_formatters.DeepSeekReasoningFormatter
        ),
        string_content_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
            provider_chat_formatters.StringContentOpenAIFormatter
        ),
        kimi_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
            provider_chat_formatters.KimitChatFormatter
        ),
        default_formatter: typing.Callable[[int], message_formatters.MessageFormatter] = (
            provider_chat_formatters.TruncatingOpenAIChatFormatter
        ),
    ) -> None:
        self._model_type = model_type
        self._deepseek_formatter = deepseek_formatter
        self._string_content_formatter = string_content_formatter
        self._kimi_formatter = kimi_formatter
        self._default_formatter = default_formatter

    @property
    def model_type(self) -> type[retry_policy.NativeOpenAiChatModel]:
        return self._model_type

    @property
    def model_label(self) -> str:
        return "NativeOpenAiChatModel"

    def create_formatter(self, *, model_name: object, tool_args_compression_limit: int) -> message_formatters.MessageFormatter:
        if isinstance(model_name, str):
            model_name_lower = model_name.lower()
            if openai_compatible_thinking.is_deepseek_model(model_name):
                return self._deepseek_formatter(tool_args_compression_limit=tool_args_compression_limit)
            if any(key in model_name_lower for key in ("qwen", "glm")):
                return self._string_content_formatter(tool_args_compression_limit=tool_args_compression_limit)
            if any(key in model_name_lower for key in ("kimi", "moonshot", "k2")):
                return self._kimi_formatter(tool_args_compression_limit=tool_args_compression_limit)

            return self._default_formatter(tool_args_compression_limit=tool_args_compression_limit)
        return self._default_formatter(tool_args_compression_limit=tool_args_compression_limit)


class ResponsesProtocolStrategy(OpenAIProtocolStrategy):

    @property
    def model_type(self) -> type[retry_policy.NativeOpenAiChatModel]:
        return openai_responses_model.OpenAIResponsesModel

    @property
    def model_label(self) -> str:
        return "OpenAIResponsesModel"

    def create_formatter(self, *, model_name: object, tool_args_compression_limit: int) -> message_formatters.MessageFormatter:
        del model_name
        return openai_responses_formatter.OpenAIResponsesFormatter(tool_args_compression_limit=tool_args_compression_limit)


_RESPONSE_STRATEGY = ResponsesProtocolStrategy()



