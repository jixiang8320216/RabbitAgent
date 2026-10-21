"""domains.llm.provider.application 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "ArgumentCompressor": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "ArgumentMarkerFixer": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "ClaudeContextWindowStrategy": "domains.llm.provider.application.context.window_rules",
    "ConfigurationSource": "domains.llm.provider.application.configuration.runtime_resolution",
    "DEFAULT_CONTEXT_WINDOW": "domains.llm.provider.application.context.context_window_resolver",
    "DeepSeekReasoningFormatter": "domains.llm.provider.application.formatting.chat_formatters",
    "DynamicToolListFormatter": "domains.llm.provider.application.formatting.tool_argument_formatter",
    "FormattedMessages": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "FormatterCacheKey": "domains.llm.provider.application.model.adapter_factory",
    "GeminiContextWindowStrategy": "domains.llm.provider.application.context.window_rules",
    "GlmContextWindowStrategy": "domains.llm.provider.application.context.window_rules",
    "JsonObject": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "KimiMiniMaxContextWindowStrategy": "domains.llm.provider.application.context.window_rules",
    "KimitChatFormatter": "domains.llm.provider.application.formatting.chat_formatters",
    "LLMModelAccessService": "domains.llm.provider.application.model.adapter_factory",
    "LLMProviderAdapter": "domains.llm.provider.application.capability.adapter_port",
    "LLMProviderFactory": "domains.llm.provider.application.capability.provider_factory",
    "LLMRuntimeConfigurationResolver": "domains.llm.provider.application.configuration.runtime_resolution",
    "MODELS_DEV_CATALOG_CONFIG_KEYS": "domains.llm.provider.application.context.context_window_resolver",
    "MODELS_DEV_CATALOG_DIR": "domains.llm.provider.application.context.context_window_resolver",
    "MODELS_DEV_CATALOG_ENV_VAR": "domains.llm.provider.application.context.context_window_resolver",
    "MODELS_DEV_CATALOG_FILENAME": "domains.llm.provider.application.context.context_window_resolver",
    "MODELS_DEV_MODELS_FILENAME": "domains.llm.provider.application.context.context_window_resolver",
    "MemoryTokenSink": "domains.llm.provider.application.token.token_counter",
    "ModelCacheKey": "domains.llm.provider.application.model.adapter_factory",
    "MultimodalConfigurationSource": "domains.llm.provider.application.model.multimodal_configuration",
    "NoOpRequestAttestor": "domains.llm.provider.application.authentication.request_attestation",
    "OpenAICodexResponsesFormatter": "domains.llm.provider.application.formatting.codex_formatter",
    "OpenAICompatibleModeFamily": "domains.llm.provider.application.formatting.compatible_thinking",
    "OpenAICompatibleRequestContext": "domains.llm.provider.application.formatting.compatible_thinking",
    "OpenAICompatibleRequestOptions": "domains.llm.provider.application.formatting.compatible_thinking",
    "OpenAIContextWindowStrategy": "domains.llm.provider.application.context.window_rules",
    "OpenAILocalTokenCounter": "domains.llm.provider.application.token.openai_token_counting",
    "OpenAIMessageFormatter": "domains.llm.provider.application.formatting.message_formatters",
    "OpenAIResponsesFormatter": "domains.llm.provider.application.formatting.responses_formatter",
    "ProviderAdapterFactory": "domains.llm.provider.application.model.adapter_factory",
    "ProviderAdapterRegistration": "domains.llm.provider.application.capability.provider_module",
    "ProviderAdapterType": "domains.llm.provider.application.capability.provider_module",
    "ProviderAuthHook": "domains.llm.provider.application.authentication.provider_authentication",
    "ProviderAuthHookFactory": "domains.llm.provider.application.capability.provider_module",
    "ProviderAuthResult": "domains.llm.provider.application.authentication.provider_authentication",
    "ProviderAuthenticationCapability": "domains.llm.provider.application.capability.provider_module",
    "ProviderAuthenticationEnvironment": "domains.llm.provider.application.capability.provider_module",
    "ProviderAuthenticationHookRequest": "domains.llm.provider.application.capability.provider_module",
    "ProviderAuthenticationRuntimePort": "domains.llm.provider.application.capability.provider_module",
    "ProviderCapabilityError": "domains.llm.provider.application.capability.provider_module",
    "ProviderCapabilityLifecycle": "domains.llm.provider.application.capability.provider_module",
    "ProviderCatalogIdentityCacheUpate": "domains.llm.provider.application.capability.provider_module",
    "ProviderConfigurationPolicyType": "domains.llm.provider.application.configuration.registry",
    "ProviderConfigurationRegistry": "domains.llm.provider.application.configuration.registry",
    "ProviderCredentialExchangePort": "domains.llm.provider.application.capability.provider_module",
    "ProviderMessageFormatter": "domains.llm.provider.application.formatting.message_formatters",
    "ProviderModeCatalogCapability": "domains.llm.provider.application.capability.provider_module",
    "ProviderModelCatalogPort": "domains.llm.provider.application.capability.provider_module",
    "ProviderModule": "domains.llm.provider.application.capability.provider_module",
    "ProviderModuleCapabilities": "domains.llm.provider.application.capability.provider_module",
    "ProviderModuleRegistry": "domains.llm.provider.application.capability.module_registry",
    "ProviderMultimodalCapability": "domains.llm.provider.application.capability.provider_module",
    "ProviderMultimodalProjector": "domains.llm.provider.application.model.multimodal_configuration",
    "ProviderOAuthServicePort": "domains.llm.provider.application.capability.provider_module",
    "ProviderProfileEnvironmentSStamper": "domains.llm.provider.application.capability.provider_module",
    "ProviderProfileEnvironmentValidator": "domains.llm.provider.application.capability.provider_module",
    "ProviderProfileMigrator": "domains.llm.provider.application.capability.provider_module",
    "ProviderQuotaRequest": "domains.llm.provider.application.capability.provider_module",
    "ProviderQuotaUsageCapability": "domains.llm.provider.application.capability.provider_module",
    "ProviderQuotaUsagePort": "domains.llm.provider.application.capability.provider_module",
    "ProviderQuotaWindow": "domains.llm.provider.application.capability.provider_module",
    "ProviderRequestAttestationCapability": "domains.llm.provider.application.capability.provider_module",
    "ProviderRequestAttestationError": "domains.llm.provider.application.authentication.request_attestation",
    "ProviderRequestAttestorProtocol": "domains.llm.provider.application.authentication.request_attestation",
    "ProviderServiceZoneCapability": "domains.llm.provider.application.capability.provider_module",
    "QwenContextWindowStrategy": "domains.llm.provider.application.context.window_rules",
    "QwenThinkingCapability": "domains.llm.provider.application.model.capability_resolution",
    "StringContentOpenAIFormatter": "domains.llm.provider.application.formatting.chat_formatters",
    "ThinkingInputParts": "domains.llm.provider.application.capability.thinking_input",
    "ThinkingPayloadeRoute": "domains.llm.provider.application.formatting.compatible_thinking",
    "TokenCounter": "domains.llm.provider.application.token.token_counter",
    "TokenCounterBase": "domains.llm.provider.application.token.openai_token_counting",
    "TokenExtractor": "domains.llm.provider.application.token.usage_extraction",
    "ToolMessageIndex": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "ToolNameInferrer": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "TruncatingOpenAIChatFormatter": "domains.llm.provider.application.formatting.chat_formatters",
    "as_formatted_message": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "as_formatted_messages": "domains.llm.provider.application.capability.thinking_input",
    "ensure_default_configuration_registered": "domains.llm.provider.application.configuration.registry",
    "extract_llm_configuration": "domains.llm.provider.application.configuration.runtime_resolution",
    "fix_glm_markers_in_messages": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "format_cli_user_message_error": "domains.llm.provider.application.authentication.error_message",
    "get_context_window_for_model": "domains.llm.provider.application.context.context_window_resolver",
    "get_effective_context_window_for_model": "domains.llm.provider.application.context.context_window_resolver",
    "get_formatter": "domains.llm.provider.application.formatting.formatter_creation",
    "get_model": "domains.llm.provider.application.model.chat_model_creation",
    "get_qwen_thinking_capability": "domains.llm.provider.application.model.capability_resolution",
    "get_qwen_thinking_default": "domains.llm.provider.application.model.capability_resolution",
    "is_deepseek_model": "domains.llm.provider.application.model.capability_resolution",
    "is_glm_thinking_model": "domains.llm.provider.application.model.capability_resolution",
    "is_kimi_k3_model": "domains.llm.provider.application.model.capability_resolution",
    "is_minimax_thinking_model": "domains.llm.provider.application.model.capability_resolution",
    "is_qwen_thinking_model": "domains.llm.provider.application.model.capability_resolution",
    "is_rabbit_litellm_endpoint": "domains.llm.provider.application.formatting.compatible_thinking",
    "migrate_provider_profile": "domains.llm.provider.application.configuration.profile_migration",
    "normalize_deepseek_reasoning_effort": "domains.llm.provider.application.formatting.compatible_thinking",
    "project_thinking_input": "domains.llm.provider.application.capability.thinking_input",
    "repair_formatted_tool_messages": "domains.llm.provider.application.formatting.tool_message_pipeline",
    "resolve_builtin_context_window": "domains.llm.provider.application.context.window_rules",
    "resolve_openai_compatible_request": "domains.llm.provider.application.formatting.compatible_thinking",
    "resolve_profile_proxy_configuration": "domains.llm.provider.application.configuration.resolution",
    "resolve_temperature": "domains.llm.provider.application.formatting.compatible_thinking",
    "split_tool_identity": "domains.llm.provider.application.formatting.responses_formatter",}


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
