"""按 provider 配置造出主循环可用的模型对象。

``llm.provider.composition`` 把 ``get_model`` / ``get_formatter`` 委托到这里，
装配链是::

    ModelContextFactory.create_model
        -> llm.provider.composition.get_model
            -> chat_model_creation.get_model
                -> runtime.service.chart_model.OpenAICompatibleChatModel

**为什么不能直接 new ``runtime.service.chart_model.ChatModel``**：那个类是
抽象基类（``abc.ABC`` + ``@abc.abstractmethod __call__``）且没有 ``__init__``，
``ChatModel(provider=..., ...)`` 会落到 ``object.__init__`` 并抛
``TypeError: ChatModel() takes no arguments``。装配期真正要拿到的是它的具体
实现。

**为什么走 OpenAI 兼容客户端而不是 provider adapter 链**：adapter 链目前还缺
两环——``LLMProviderFactory`` 只有 ``register`` / ``get_available_providers``
（没有 ``create``），且各 family 的 ``*ChatModel``（如
``NativeAnthropicChatModel``）只有类声明，``__call__`` 会落到
``foundation/chat_tranport/chat_model.py`` 的 ``NotImplementedError``，发不出
真实请求。等这两环补齐后，本函数是**唯一的切换点**：在这里按 ``provider``
取 adapter、调 ``adapter.create_model(...)`` 即可，调用方不用动。

返回的模型具备主循环 ``ReactReplyOrchestrator._call_model`` 需要的形状：
可调用（``await model(messages, tools)`` 返回纯文本），并提供
``ainvoke`` / ``achat`` / ``agenerate`` 别名。
"""

from __future__ import annotations

import logging
import collections.abc as abc

import domains.llm.provider.application.token.token_counter as token_counter_module
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.llm.provider.application.capability.provider_factory as provider_factory
import domains.llm.provider.application.configuration.profile_migration as provider_profile_migration
from domains.llm.provider.domain import openai_app_protocol, runtime_configuration, primary_profile_selection
import domains.llm.provider.domain.provider_service_zone as provider_service_zone
import domains.llm.provider.domain.provider_identity as provider_identity

logger = logging.getLogger(__name__)

get_effective_runtime_config = execution_context.get_effective_runtime_config

__all__ = ["get_model"]


def _coerce_api_protocol(
        value: openai_app_protocol.OpenAIAPIProtocol|str|None,
)-> openai_app_protocol.OpenAIAPIProtocol|None:
    if value is None:
        return None
    if isinstance(value, openai_app_protocol.OpenAIAPIProtocol):
        return value
    return openai_app_protocol.OpenAIAPIProtocol(value)


# --------------------------------------------------------------------------
# profile 字段读取
#
# ``get_model`` 允许调用方只传部分参数，缺的那些从当前生效的 profile
# （``llm_configs`` 里的 active 项）补齐。profile 是外部配置，**形状不可信**：
# 同一个字段可能写成驼峰（``useProxy``）也可能写成下划蛇形（``use_proxy``），
# 值可能是 ``"true"`` 也可能是 ``True`` / ``1``。这四个方法只做「取值 + 归一
# 化」，取不到或认不出一律返回 ``None``——**不抛异常**，让调用方的
# ``if xxx is None:`` 兜底逻辑继续生效。
# --------------------------------------------------------------------------

_TRUE_LITERALS = frozenset({"true", "1", "yes", "on", "y", "enabled", "enable"})
_FALSE_LITERALS = frozenset({"false", "0", "no", "off", "n", "disabled", "disable"})

_API_PROTOCOL_ALIASES = {
    "chat_completions": "chat_completions",
    "chatcompletion": "chat_completions",
    "chat_completion": "chat_completions",
    "chat": "chat_completions",
    "openai": "chat_completions",
    "responses": "responses",
    "response": "responses",
    "responses_api": "responses",
    "response_api": "responses",
}


def _profile_bool(
    profile: abc.Mapping[str, runtime_configuration.JsonValue],
    *keys: str,
) -> bool | None:
    """按 ``keys`` 顺序取布尔字段；取不到或认不出返回 ``None``。

    认 ``True/False``、``1/0``、``"true"/"false"/"yes"/"no"/"on"/"off"``
    （大小写不敏感），其余形状跳过。

    注意：显式写下的 ``False`` **必须**原样返回。原实现写成
    ``value if isinstance(value, bool) and value else None``，
    那个 ``and value`` 会把 ``False`` 一起吞成 ``None``——「用户明确关闭」
    和「用户没配」被混为一谈。调用方正是靠这个 ``None`` 来区分三态的
    （``None`` = 沿用模型默认），一旦被污染，思考模型就会被误判成
    「已禁用思考」。
    """
    value = _profile_value(profile, *keys)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_LITERALS:
            return True
        if lowered in _FALSE_LITERALS:
            return False
    return None


def _profile_value(
    profile: abc.Mapping[str, runtime_configuration.JsonValue],
    *keys: str,
) -> runtime_configuration.JsonValue | None:
    for key in keys:
        if key in profile:
            return profile[key]
    return None

def _profile_string(
    profile: abc.Mapping[str, runtime_configuration.JsonValue],
    *keys: str,
) -> str | None:
   value = _profile_value(profile,*keys)
   return value if isinstance(value,str) and  value else None


def _profile_int(
    profile:  abc.Mapping[str, runtime_configuration.JsonValue],
    *keys: str,
) -> int | None:
    """按 ``keys`` 顺序取整数字段；取不到或解析不了返回 ``None``。

    字符串按 ``int`` 解析，失败再试一次 ``float``（兼容 ``"2048.0"``）。
    布尔不是合法整数配置，跳过。
    """
    value = _profile_value(profile, *keys)
    return value if type(value) is int else None

def _coerce_api_protocol(
    value: openai_app_protocol.OpenAIAPIProtocol|str|None,
)-> openai_app_protocol.OpenAIAPIProtocol | None:
    if value is None:
        return None
    if isinstance(value, openai_app_protocol.OpenAIAPIProtocol):
        return value
    return openai_app_protocol.OpenAIAPIProtocol(value)


def _profile_api_protocol(
    profile:  abc.Mapping[str, runtime_configuration.JsonValue],
) -> openai_app_protocol.OpenAIAPIProtocol | None:

    return _coerce_api_protocol(_profile_string(profile, "apiProtocol","api_protocol"))

def _resolve_provider_profile(
    provider_id:str,
    service_zone:provider_service_zone.ProviderServiceZone|str,
    metadata:runtime_configuration.JsonObject|None,
)->tuple[str, provider_service_zone.ProviderServiceZone, runtime_configuration.JsonObject|None]:
    resolved_zone = provider_service_zone.ProviderServiceZone.GREEN
    profile:runtime_configuration.JsonObject={
        "provider": provider_id,
        "service_zone": service_zone,
    }
    if metadata is not None:
        profile["metadata"] = metadata
    migrated = provider_profile_migration.migrate_provider_profile(profile)
    migrated_provider= migrated.get("provider")
    migrated_zone = migrated.get("service_zone", resolved_zone.value)
    if not isinstance(migrated_provider,str):
        migrated_provider = provider_id
    migrated_metadata = migrated.get("metadata")
    resolved_metadata=(
        runtime_configuration.validate_json_object(migrated_metadata) if isinstance(migrated_metadata,dict) else None
    )

    return(
        migrated_provider,
        provider_service_zone.ProviderServiceZone(migrated_zone),
        resolved_metadata,
    )


def get_model(
    provider_name: str,
    baseUrl: str | None,
    modelName: str,
    apiKey: str | None,
    token_counter: token_counter_module.TokenCounter | None = None,
    agent_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort:str | None = None,
    service_tier:str | None = None,
    api_protocol: openai_app_protocol.OpenAIAPIProtocol|str | None = None,
    metadata: runtime_configuration.JsonObject | None = None,
    http_proxy: str | None = None,
    https_proxy: str | None = None,
    use_proxy: bool | None = None,
    proxy_username: str | None = None,
    proxy_password: str | None = None,
    thinking_mode:bool | None = None,
    service_zone: provider_service_zone.ProviderServiceZone |str = provider_service_zone.ProviderServiceZone.GREEN,
    allow_active_profile_fallback:bool =True,
    custom_headers:dict[str, str] | None = None,
) -> chat_model.ChatModel:
    """造一个可用的模型对象。

    :param provider: provider 名（``anthropic`` / ``openai`` / ``custom`` …），
        当前只作为分类维度与日志上下文保存。
    :param base_url: 服务地址；``None`` 时由客户端自己从环境变量兜底。
    :param model_name: 模型名。
    :param api_key: 凭据；``None`` 时由客户端自己从环境变量兜底。
    :param token_counter: token 计数器，由 ``ModelContextFactory`` 注入。
    :param agent_name: 归属 agent，用于日志/观测。
    :param observer: 运行时观测器；``None`` 时模型内部退到空实现。
    :param model_kwargs: 透传给模型的生成参数与传输选项
        （``temperature`` / ``max_tokens`` / ``reasoning_effort`` /
        ``http_proxy`` / ``custom_headers`` …）。

    装配期**不抛异常**：凭据不全只会在**首次真正调用**时才报错，这样缺模型
    配置不会让启动失败。
    """
    if not provider_name:
        raise RuntimeError("LLM model configuraion error provider_name")
    normalized_llm_provider, resolved_server_zone, resolved_metadata = _resolve_provider_profile(
        provider_identity.normalize_provider_name(provider_name),
        service_zone,
         metadata,)

    if normalized_llm_provider not in provider_identity.PROVIDER_MANAGED_BASE_URL_PROVIDERS and not baseUrl:
        raise RuntimeError("llm model configuration error normalized_llm_provider")
    if not modelName:
        raise RuntimeError("llm model configuration error modelName")
    if allow_active_profile_fallback and any(
        p is None
        for p in (
            use_proxy,
            http_proxy,
            https_proxy,
            proxy_username,
            proxy_password,
            max_tokens,
            reasoning_effort,
            service_tier,
            api_protocol,
            thinking_mode,
            custom_headers,
        )
    ):
        use_config = get_effective_runtime_config()
        profiles = use_config.get("llmConfigs")
        if not isinstance(profiles, list):
            profiles =use_config.get("llm_configs")
        if not isinstance(profiles, list):
            profiles = []
        profile_configs: list[runtime_configuration.JsonObject]=[]
        for profile in profiles:
            if not isinstance(profile, abc.Mapping):
                raise RuntimeError("llm model configuration error profile")
            profile_configs.append(runtime_configuration.validate_json_object(profile))
        profile_config = primary_profile_selection.select_preferred_active_mapping(profile_configs)
        if profile_config is not None:
            if use_proxy is None:
                use_proxy = _profile_bool(profile_config, "useProxy", "use_proxy")
            if http_proxy is None:
                http_proxy =_profile_string(profile_config, "httpProxy", "http_proxy")
            if https_proxy is None:
                https_proxy = _profile_string(profile_config, "httpsProxy", "https_proxy")
            if proxy_username is None:
                proxy_username = _profile_string(profile_config,"proxyUsername", "proxy_username")
            if proxy_password is None:
                profile_password =  _profile_string(profile_config,"proxyPassword", "proxy_password")
            if max_tokens is None:
                max_tokens= _profile_int(profile_config,"maxTokens", "max_tokens")
            if reasoning_effort is None:
                reasoning_effort = _profile_string(profile_config,"reasoningEffort", "reasoning_effort")
            if service_tier is None:
                service_tier = _profile_string(profile_config,
                                               "serviceTier", "service_tier","fastMode","fast_mode")
            if api_protocol is None:
                api_protocol= _profile_api_protocol(profile_config)
            if thinking_mode is None:
                thinking_mode= _profile_bool(profile_config,"thinkingMode", "thinking_mode")
            if custom_headers is None:
                ch= profile_config.get("customHeaders") or profile_config.get("custom_headers")
                if isinstance(ch, dict):
                    custom_headers = {str(k) : str(v) for k, v in ch.items()}

    resolved_api_protocol = _coerce_api_protocol(api_protocol) or openai_app_protocol.OpenAIAPIProtocol.CHAT_COMPLETIONS
    config = runtime_configuration.LLMRuntimeConfiguration(
        provider=normalized_llm_provider,
        service_zone = resolved_server_zone,
        api_protocol= resolved_api_protocol,
        model_name= modelName,
        base_url= baseUrl,
        api_key= apiKey,
        use_proxy= use_proxy,
        http_proxy= http_proxy,
        https_proxy= https_proxy,
        proxy_username= proxy_username,
        proxy_password=proxy_password,
        temperature= temperature,
        max_tokens= max_tokens,
        reasoning_effort= reasoning_effort,
        service_tier =service_tier,
        metadata = resolved_metadata,
        thinking_mode= thinking_mode,
        custom_headers= custom_headers,
    )
    factory = provider_factory.LLMProviderFactory()
    provider =factory.create(normalized_llm_provider, config)
    model = provider.create_model(
        token_counter=token_counter,
        agent_name=agent_name,
        temperature=temperature,
    )
    return model
