"""domains.llm.provider.infrastructure.foundation.chat_transport 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AsyncGeneratorClosePort": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AsyncProviderCall": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AsyncProviderClientPort": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AuthHook": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "AuthHookProjection": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "ChatModel": "domains.llm.provider.infrastructure.foundation.chat_transport.chat_model",
    "ErrorResponseCarrier": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "JsonObject": "domains.llm.provider.infrastructure.foundation.chat_transport.chat_model",
    "ModelIdentifyResolver": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "ProviderChatModelBase": "domains.llm.provider.infrastructure.foundation.chat_transport.provider_chat_model",
    "ProviderFailurePolicy": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "ProviderOptions": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "ProviderPermanentError": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "ProviderRetryCoordinator": "domains.llm.provider.infrastructure.foundation.chat_transport.retry_coordinator",
    "RETRYABLE_STATUS_CODES": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "RequestTransformer": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "chat_response_has_business_output": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "close_latest_provider_client": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "close_provider_client": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "close_provider_clients": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "closer_async_generator": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "log_observer_callback_failure": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "permanent_failure": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "propagate_stream_cancellation": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "provider_error_response": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "provider_error_status": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",
    "raise_safe_permanent_error": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "retry_cause": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "safe_permanent_error": "domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification",
    "tool_call_observer_projection": "domains.llm.provider.infrastructure.foundation.chat_transport.contracts",}


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
