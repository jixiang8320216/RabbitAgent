"""domains.agent.domain.value_objects 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentConfig": "domains.agent.domain.value_objects.agent_config",
    "AgentPromptProvenance": "domains.agent.domain.value_objects.agent_execution",
    "AudioBlock": "domains.agent.domain.value_objects.message",
    "Base64Source": "domains.agent.domain.value_objects.message",
    "ChatHeadersInput": "domains.agent.domain.value_objects.plugin_hooks",
    "ChatHeadersOutput": "domains.agent.domain.value_objects.plugin_hooks",
    "ChatParamsInput": "domains.agent.domain.value_objects.plugin_hooks",
    "ChatParamsOutput": "domains.agent.domain.value_objects.plugin_hooks",
    "ChatResponse": "domains.agent.domain.value_objects.chat_response",
    "ChatStopDetails": "domains.agent.domain.value_objects.chat_response",
    "ChatUsage": "domains.agent.domain.value_objects.chat_response",
    "ContentBlock": "domains.agent.domain.value_objects.message",
    "ContentBlockTypes": "domains.agent.domain.value_objects.message",
    "Context": "domains.agent.domain.value_objects.conversation_context",
    "ContextEntry": "domains.agent.domain.value_objects.conversation_context",
    "ContextMark": "domains.agent.domain.value_objects.conversation_context",
    "ContextMessageState": "domains.agent.domain.value_objects.conversation_context",
    "ContextPersistenceIndeterminateError": "domains.agent.domain.value_objects.conversation_context",
    "ContextState": "domains.agent.domain.value_objects.conversation_context",
    "ImageBlock": "domains.agent.domain.value_objects.message",
    "JsonObject": "domains.agent.domain.value_objects.chat_response",
    "MediaSource": "domains.agent.domain.value_objects.message",
    "MessageRole": "domains.agent.domain.value_objects.message",
    "MessageTransformInput": "domains.agent.domain.value_objects.plugin_hooks",
    "MessageTransformOutput": "domains.agent.domain.value_objects.plugin_hooks",
    "Msg": "domains.agent.domain.value_objects.message",
    "PendingToolCall": "domains.agent.domain.value_objects.tool_call",
    "ProviderRetryState": "domains.agent.domain.value_objects.provider_retry_status",
    "ProviderRetryStatus": "domains.agent.domain.value_objects.provider_retry_status",
    "RedactedThinkingBlock": "domains.agent.domain.value_objects.message",
    "RuntimeContext": "domains.agent.domain.value_objects.runtime_context",
    "StreamContentBlock": "domains.agent.domain.value_objects.message",
    "TextBlock": "domains.agent.domain.value_objects.message",
    "ThinkingBlock": "domains.agent.domain.value_objects.message",
    "ToolCallResolution": "domains.agent.domain.value_objects.tool_call",
    "ToolCallStreamUpdate": "domains.agent.domain.value_objects.tool_call",
    "ToolDeltaRoutingFailure": "domains.agent.domain.value_objects.tool_call",
    "ToolInputError": "domains.agent.domain.value_objects.tool_call",
    "ToolInputStreamStats": "domains.agent.domain.value_objects.tool_call",
    "ToolLoopHit": "domains.agent.domain.value_objects.tool_loop",
    "ToolMetadata": "domains.agent.domain.value_objects.tool_response",
    "ToolMetadataValue": "domains.agent.domain.value_objects.tool_response",
    "ToolResponse": "domains.agent.domain.value_objects.tool_response",
    "ToolResultBlock": "domains.agent.domain.value_objects.message",
    "ToolResultOutput": "domains.agent.domain.value_objects.message",
    "ToolUseBlock": "domains.agent.domain.value_objects.message",
    "ToolUseDeltaBlock": "domains.agent.domain.value_objects.message",
    "URLSource": "domains.agent.domain.value_objects.message",
    "VideoBlock": "domains.agent.domain.value_objects.message",
    "content_block_from_dict": "domains.agent.domain.value_objects.message",
    "get_runtime_context": "domains.agent.domain.value_objects.runtime_context",
    "get_runtime_context_or_none": "domains.agent.domain.value_objects.runtime_context",
    "get_turn_id": "domains.agent.domain.value_objects.turn_identity",
    "get_user_config_dir": "domains.agent.domain.value_objects.runtime_context",
    "get_user_config_file": "domains.agent.domain.value_objects.runtime_context",
    "get_user_data_home": "domains.agent.domain.value_objects.runtime_context",
    "is_content_block": "domains.agent.domain.value_objects.message",
    "is_stream_content_block": "domains.agent.domain.value_objects.message",
    "reset_turn_id": "domains.agent.domain.value_objects.turn_identity",
    "set_runtime_context": "domains.agent.domain.value_objects.runtime_context",
    "set_turn_id": "domains.agent.domain.value_objects.turn_identity",
    "stream_content_block_from_mapping": "domains.agent.domain.value_objects.message",}


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
