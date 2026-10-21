"""domains.llm.provider.infrastructure.families.anthropic 聚合包。

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
    "DEFAULT_SYSTEM_SEPARATOR": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "InterruptibleSleep": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "MAX_CACHE_BREAKPOINTS": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "MODULE": "domains.llm.provider.infrastructure.families.anthropic._types",
    "NativeAnthropicChatModel": "domains.llm.provider.infrastructure.families.anthropic.chat_model",
    "ResponseGenerator": "domains.llm.provider.infrastructure.families.anthropic.request_contracts",
    "StopReasonMap": "domains.llm.provider.infrastructure.families.anthropic.response",
    "convert_tools": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "event_to_chat_response": "domains.llm.provider.infrastructure.families.anthropic.response",
    "map_stop_reason": "domains.llm.provider.infrastructure.families.anthropic.response",
    "mark_last_message_cacheable": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "message_to_chat_response": "domains.llm.provider.infrastructure.families.anthropic.response",
    "split_messages": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "split_system_into_blocks": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "stablize_tool_order": "domains.llm.provider.infrastructure.families.anthropic.messages",
    "stop_details_from_sdk": "domains.llm.provider.infrastructure.families.anthropic.response",}


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
