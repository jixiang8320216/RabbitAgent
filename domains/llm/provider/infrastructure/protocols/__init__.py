"""domains.llm.provider.infrastructure.protocols 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "ChatCompletionsProtocolStrategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "JsonObject": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "NativeOpenAIChatModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.retry_policy",
    "NativeOpenAiChatModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.chat_model",
    "OpenAIChatPort": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAIClientPort": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAICompatibleAdapterBase": "domains.llm.provider.infrastructure.protocols.openai_compatible.adapter",
    "OpenAICompletionPort": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAIProtocolStrategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "OpenAIRequestExecutor": "domains.llm.provider.infrastructure.protocols.openai_compatible.request",
    "OpenAIRequestModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "OpenAIResponsesModel": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_model",
    "OpenAIResponsesObservation": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_observation",
    "OpenAIStreamResponse": "domains.llm.provider.infrastructure.protocols.openai_compatible.stream",
    "ResponseGenerator": "domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts",
    "ResponsesProtocolStrategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "convert_tool_choice": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "convert_tools": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "event_chunks": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "log_observer_callback_failure": "domains.llm.provider.infrastructure.protocols.openai_compatible.projections",
    "prompt_cache_key": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "resolve_openai_protocol_strategy": "domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy",
    "response_chunk": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "split_instructions": "domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection",
    "tool_call_observer_projection": "domains.llm.provider.infrastructure.protocols.openai_compatible.projections",
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
