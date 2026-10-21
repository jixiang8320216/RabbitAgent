"""domains.llm.provider.infrastructure 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AnthropicBetaPort": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "AnthropicClientPort": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "AnthropicMessagesPort": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "AnthropicProvider": "domains.llm.provider.infrastructure.families.anthropic.provider",
    "AnthropicRequestExecutor": "domains.llm.provider.infrastructure.families.anthropic.request",
    "AnthropicRequestModel": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "AnthropicStreamResponse": "domains.llm.provider.infrastructure.families.anthropic.stream",
    "AsyncGeneratorClosePort": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AsyncProviderCall": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AsyncProviderClientPort": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AuthHook": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AuthHookProjection": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AzureProvider": "domains.llm.provider.infrastructure.families.azure_openai.provider",
    "BUILTIN_PROVIDER_MODULES": "domains.llm.provider.infrastructure.composition._types",
    "ChatCompletionsProtocolStrategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "ChatModel": "domains.llm.provider.infrastructure.foundation.chat_transport.chat_model",
    "CustomProvider": "domains.llm.provider.infrastructure.families.openai_compatible.legacy_custom_provider",
    "DEFAULT_PROVIDER_ID": "domains.llm.provider.infrastructure.families.openai_compatible.module",
    "DEFAULT_SYSTEM_SEPARATOR": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "ErrorResponseCarrier": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "InterruptibleSleep": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "JsonObject": "domains.llm.provider.infrastructure.foundation.chat_transport.chat_model",
    "LLMProviderAdapterBase": "domains.llm.provider.infrastructure.foundation.adapter.foundation",
    "MAX_CACHE_BREAKPOINTS": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "MODULE": "domains.llm.provider.infrastructure.families.anthropic._types",
    "ModelIdentifyResolver": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "NativeAnthropicChatModel": "domains.llm.provider.infrastructure.families.anthropic.chat_model",
    "NativeOpenAIChatModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.retry_policy",
    "NativeOpenAiChatModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.chat_model",
    "OpenAIChatPort": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAIClientPort": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAICodexProvider": "domains.llm.provider.infrastructure.families.openai_codex.provider",
    "OpenAICompatibleAdapterBase": "domains.llm.provider.infrastructure.protocols.openai_compatible.adapter",
    "OpenAICompletionPort": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAIProtocolStrategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "OpenAIProvider": "domains.llm.provider.infrastructure.families.openai_compatible.provider",
    "OpenAIRequestExecutor": "domains.llm.provider.infrastructure.protocols.openai_compatible.request",
    "OpenAIRequestModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAIResponsesModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_model",
    "OpenAIResponsesObservation": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_observation",
    "OpenAIStreamResponse": "domains.llm.provider.infrastructure.protocols.openai_compatible.stream",
    "ProviderChatModelBase": "domains.llm.provider.infrastructure.foundation.chat_transport.provider_chat_model",
    "ProviderFailurePolicy": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "ProviderOptions": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "ProviderPermanentError": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "ProviderRetryCoordinator": "domains.llm.provider.infrastructure.foundation.chat_transport.retry_coordinator",
    "RETRYABLE_STATUS_CODES": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "RequestTransformer": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "ResponseGenerator": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "ResponsesProtocolStrategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "StopReasonMap": "domains.llm.provider.infrastructure.families.anthropic.response",
    "chat_response_has_business_output": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "close_latest_provider_client": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "close_provider_client": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "close_provider_clients": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "closer_async_generator": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "convert_tool_choice": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "convert_tools": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "ensure_all_providers_registered": "domains.llm.provider.infrastructure.composition.registration",
    "event_chunks": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "event_to_chat_response": "domains.llm.provider.infrastructure.families.anthropic.response",
    "log_observer_callback_failure": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "map_stop_reason": "domains.llm.provider.infrastructure.families.anthropic.response",
    "mark_last_message_cacheable": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "message_to_chat_response": "domains.llm.provider.infrastructure.families.anthropic.response",
    "permanent_failure": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "prompt_cache_key": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "propagate_stream_cancellation": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "provider_error_response": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "provider_error_status": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "raise_safe_permanent_error": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "register_all_providers": "domains.llm.provider.infrastructure.composition.registration",
    "resolve_openai_protocol_strategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "response_chunk": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "retry_cause": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "safe_permanent_error": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "split_instructions": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "split_messages": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "split_system_into_blocks": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "stablize_tool_order": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "stop_details_from_sdk": "domains.llm.provider.infrastructure.families.anthropic.response",
    "tool_call_observer_projection": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "usage": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "validate_json_object": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",}


def __getattr__(name):
    import importlib

    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
