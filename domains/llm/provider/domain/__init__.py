"""domains.llm.provider.domain 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
# 注：Azure OpenAI / OpenAI Codex 的 provider 与 provider_configuration 此前被登记在
# 本表里，但目标模块（``...azure_openai...`` / ``...openai_codex...``）从未迁移过来：
# 这是一张「声明了实现、访问才报错」的坏映射。与其继续留着一颗雷，不如显式摘除——
# 真正需要这两个 provider 时应当按 openai_compatible 同族的形态**实现后**再登记。
_LAZY = {
    "AnthropicProviderConfiguration": "domains.llm.provider.domain.anthropic.provider_configuration",
    "AuthType": "domains.llm.provider.domain.provider_configuration_policy",
    "CODEAGENT_RPOVIDERS": "domains.llm.provider.domain.provider_identity",
    "CustomProviderConfiguration": "domains.llm.provider.domain.openai_compatible.legacy_custom_configuration",
    "DEFAULT_CHUNK_IDLE_TIMEOUT": "domains.llm.provider.domain.streaming_policy",
    "JsonFlatObject": "domains.llm.provider.domain.runtime_configuration",
    "JsonObject": "domains.llm.provider.domain.runtime_configuration",
    "JsonScalar": "domains.llm.provider.domain.runtime_configuration",
    "JsonValue": "domains.llm.provider.domain.runtime_configuration",
    "LEGACY_CODEMATE_YELLOW_ALIAS": "domains.llm.provider.domain.provider_identity",
    "LEGACY_CODEMATE_YELLOW_PROVIDER": "domains.llm.provider.domain.provider_identity",
    "LLMProviderProfile": "domains.llm.provider.domain.provider_profile",
    "LLMProviderType": "domains.llm.provider.domain.provider_identity",
    "LLMRuntimeConfiguration": "domains.llm.provider.domain.runtime_configuration",
    "MAX_CHUNK_IDLE_TIMEOUT": "domains.llm.provider.domain.streaming_policy",
    "MIN_CHUNK_IDLE_TIMEOUT": "domains.llm.provider.domain.streaming_policy",
    "MODEL_PERMISSION_ERROR_MARKERS": "domains.llm.provider.domain.provider_failure_markers",
    "OENAI_COMPATIBLE_LEGACY_ALIASES": "domains.llm.provider.domain.provider_identity",
    "OPENAI_COMPATIBLE_PROFILE_PROVIDERS": "domains.llm.provider.domain.provider_identity",
    "OPENAI_COMPATIBLE_PROVIDER_NAME": "domains.llm.provider.domain.provider_identity",
    "OpenAIAPIProtocol": "domains.llm.provider.domain.openai_api_protocol",
    "OpenAIProviderConfiguration": "domains.llm.provider.domain.openai_compatible.provider_configuration",
    "PROVIDER_FAILURE_CAPABILITY_ERROR_MARKERS": "domains.llm.provider.domain.provider_failure_markers",
    "PROVIDER_FAILURE_RATE_LIMIT_ERROR_MARKERS": "domains.llm.provider.domain.provider_failure_markers",
    "PROVIDER_FAILURE_SERVICE_UNAVAILABLE_ERROR_MARKERS": "domains.llm.provider.domain.provider_failure_markers",
    "PROVIDER_MANAGED_API_KEY_PROVIDERS": "domains.llm.provider.domain.provider_identity",
    "PROVIDER_MANAGED_BASE_URL_PROVIDERS": "domains.llm.provider.domain.provider_identity",
    "PROVIDER_TRANSIENT_ERROR_MARKERS": "domains.llm.provider.domain.provider_failure_markers",
    "ProviderConfigurationPolicy": "domains.llm.provider.domain.provider_configuration_policy",
    "ProviderRequestFailure": "domains.llm.provider.domain.retry_policy",
    "ProviderRetryCause": "domains.llm.provider.domain.retry_policy",
    "ProviderRetryExhaustedError": "domains.llm.provider.domain.retry_policy",
    "ProviderRetryUserFacingError": "domains.llm.provider.domain.retry_policy",
    "ProviderServiceZone": "domains.llm.provider.domain.provider_service_zone",
    "ProviderUserFacingError": "domains.llm.provider.domain.retry_policy",
    "QUOTA_ERROR_MARKERS": "domains.llm.provider.domain.provider_failure_markers",
    "generate_profile_id": "domains.llm.provider.domain.provider_identity",
    "normalize_provider_name": "domains.llm.provider.domain.provider_identity",
    "provider_failure_diagnostic_code": "domains.llm.provider.domain.retry_policy",
    "resolve_chunk_idle_timeout": "domains.llm.provider.domain.streaming_policy",
    "resolve_streaming_enabled": "domains.llm.provider.domain.streaming_policy",
    "select_preferred_active_mapping": "domains.llm.provider.domain.primary_profile_selection",
    "select_preferred_active_profile": "domains.llm.provider.domain.primary_profile_selection",
    "validate_json_object": "domains.llm.provider.domain.runtime_configuration",}


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
