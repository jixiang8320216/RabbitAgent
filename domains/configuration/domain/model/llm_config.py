import typing
from collections.abc import Mapping

import pydantic
from typing import Any, Literal
from domains.llm.provider.domain import openai_app_protocol as openai_api_protocol

# ``thinking_mode`` 里明确表示「关闭思考」的字面量。与
# ``llm.provider.application.chat_model_creation._FALSE_LITERALS`` 保持一致，
# 但**不包含** ``"disabled"`` 的误判风险：这里只认真正的假值词。
_FALSE_THINKING_LITERALS = frozenset({"false", "0", "no", "off", "n", "disable"})

type JsonObject = dict[str,pydantic.JsonValue]

class LLMConfigProvider(pydantic.BaseModel):
    """单个LLM 模型配置文件。
    用于多模型配置场景，每个profile包含一个完整的LLM 配置。

    Attribute：
       id: 唯一标识符(字符串， 如 "default","openai","claude"),用于多profile场景下的索引引用
       name:显示名称（供UI展示）
       provider: LLM提供商类型（openai、azure、anthropic）
       active:是否为当前激活的profile.多profile场景下仅一个为true
       #协议
       api_protocol: API协议类型，chat_completions(标准ChatCompletion)或responses(OpenAI Response API)
       #连接信息
       base_url: API基础URL，如https://api.openai.com/v1,空字符串表示未配置
       model_name: 模型名称 如openai/claude/deepseek
       api_key: API 密钥
       #模型行为参数
       reasoning_effort: OpenAI推理强度 low/medium/high 仅reasoning 模型生效
       service_tier: OpenAI服务等级：auto/default/flex
       max_tokens: 单词响应最大token数
       context_window_size: 上下文窗口大小（token数），用于截断历史消息
       thinking_mode: 是否启用thinking模式： null/true启用，false禁用
       #扩展字段
       metadata: 任意元数据存储，用于业务附加信息
       custom_headers: 自定http请求头，附加打破LLM API请求中
    """
    model_config = pydantic.ConfigDict(
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    id: str = "default"
    name: str = "Default Model"
    provider: str = "openai"
    api_protocol: str = pydantic.Field(
        default=openai_api_protocol.OpenAIAPIProtocol.CHAT_COMPLETIONS,
        validation_alias=pydantic.AliasChoices("api_protocol", "apiProtocol"),
        serialization_alias="apiProtocol",
    )
    base_url: str = pydantic.Field(
        default="",
        validation_alias=pydantic.AliasChoices("base_url", "baseUrl"),
        serialization_alias="baseUrl",
    )
    model_name: str = pydantic.Field(
        default="openai",
        validation_alias=pydantic.AliasChoices("model_name", "modelName"),
        serialization_alias="modelName",
    )
    api_key: str = pydantic.Field(
        default="",
        validation_alias=pydantic.AliasChoices("api_key", "apiKey"),
        serialization_alias="apiKey",
    )
    reasoning_effort: str | None = None
    service_tier: str | None = None
    max_tokens: int | None = None
    context_window_size: int | None = None
    thinking_mode: bool | str | None = None
    metadata: dict[str, Any] | None = None
    custom_headers: typing.Dict[str, str] | None = None
    active: bool = False
    temperature: float |None = 0.7

    @classmethod
    def from_dict(cls, input_data: dict[str, Any]) -> "LLMConfigProvider":
        # 修复②：先拷贝，避免原地修改调用方 dict
        data = dict(input_data)
        complete_data: dict[str, Any] = {}
        for field_name, field_info in cls.model_fields.items():
            # 先铺默认值
            if not field_info.is_required():
                complete_data[field_name] = field_info.get_default(call_default_factory=True)
            # 同轮内用字段名或别名覆盖
            if field_name in data:
                complete_data[field_name] = data.pop(field_name)
                continue
            va = field_info.validation_alias
            aliases = va.choices if isinstance(va, pydantic.AliasChoices) else (va,)
            for alias in aliases:
                if alias in data:
                    complete_data[field_name] = data.pop(alias)
                    break
        return cls.model_validate(complete_data)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)

    def to_llm_config(self) -> "LLMConfig":
        return LLMConfig(
            profile_id=self.id,
            provider_values=self.provider,
            api_protocol=self.api_protocol,
            base_url=self.base_url,
            model_name=self.model_name,
            api_key=self.api_key,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            max_tokens=self.max_tokens,
            context_window_size=self.context_window_size,
            metadata=self.metadata,
            thinking_mode=self.thinking_mode,
            custom_headers=self.custom_headers,
            temperature=self.temperature if self.temperature is not None else 0.7,
        )


def clean_llm_config_for_save(config:JsonObject)->JsonObject:
    from domains.llm.provider import composition as provider_composition
    cleaned =dict(config)
    raw_profiles = config.get("llm_configs")
    if isinstance(raw_profiles, dict):
        raw_profiles = [raw_profiles]

    if isinstance(raw_profiles,list):
        cleaned_profiles:list[JsonObject]=[]
        for profile in raw_profiles:
            if not isinstance(profile,dict):
                continue
            clean_profile = dict(profile)
            clean_profile = provider_composition.migrate_provider_profile(clean_profile)
            if "isActive" in clean_profile:
                clean_profile["active"]=clean_profile.pop("isActive")
            base_url = _profile_base_url(clean_profile)
            clean_profile.pop("apiUrl", None)
            if base_url:
                clean_profile["baseUrl"] = base_url
            clean_profile.setdefault("modelType", clean_profile.get("model_type","llm") or "llm")
            cleaned_profiles.append(clean_profile)
        _ensure_active_profile_by_model_type(cleaned_profiles)
        cleaned["llm_configs"]=cleaned_profiles
    return cleaned


def _first_non_empty(*values:pydantic.JsonValue, default: pydantic.JsonValue="")->pydantic.JsonValue:
    for value in values:
        if value is not None and value != "":
            return value
    return default

def _profile_base_url(profile: JsonObject)-> pydantic.JsonValue:
    return _first_non_empty(profile.get("baseUrl"), profile.get("base_url"), profile.get("apiUrl"),default="")

def _normalize_llm_profile(profile: JsonObject)->JsonObject:
    """规整单个 LLM profile：补全 camelCase 的 ``modelType``。
    """
    normalized = dict(profile)
    normalized.setdefault("modelType", normalized.get("model_type", "llm") or "llm")
    return normalized

def normalize_llm_config_for_read(config: JsonObject) -> JsonObject:
    """将合并配置里的 LLM profile 列表规整为内存统一格式（camelCase ``llmConfigs``），供读取/运行使用。

    做两件事：
    1. provider 别名迁移（旧字段名 -> 当前规范），并补全 ``modelType`` 等规整字段；
    2. 确保每个 ``modelType`` 分组下至少有一个 ``active`` 的 profile。

    兼容 camelCase（``llmConfigs``）与 snake_case（``llm_configs``）两种键名，
    统一输出为 camelCase（并清掉可能残留的 snake_case 键，避免重复）。
    不修改磁盘上的原始配置——返回的是浅拷贝。
    """
    # 延迟导入：provider 侧会反向依赖本模块的 LLMConfig，顶层导入会形成循环。
    from domains.llm.provider import composition as provider_composition

    normalized = dict(config)

    raw_profiles = normalized.get("llmConfigs")
    if raw_profiles is None:
        raw_profiles = normalized.get("llm_configs")

    if not isinstance(raw_profiles, list):
        return normalized

    profiles: list[JsonObject] = [
        provider_composition.migrate_provider_profile(_normalize_llm_profile(profile))
        for profile in raw_profiles
        if isinstance(profile, dict)
    ]
    if not profiles:
        return normalized

    _ensure_active_profile_by_model_type(profiles)
    normalized["llmConfigs"] = profiles
    normalized.pop("llm_configs", None)
    return normalized


def _ensure_active_profile_by_model_type(profiles:list[JsonObject])->None:
    profiles_by_type:dict[str,list[JsonObject]]={}
    #1. 按 modelType 分组（规整后 profile 必有 camelCase 的 modelType；回退 snake_case 兼容极端输入）
    for profile in profiles:
        model_type = profile.get("modelType") or profile.get("model_type") or "llm"
        profiles_by_type.setdefault(model_type, []).append(profile)
    #2. 每组确保至少一个active
    for grouped_profiles in profiles_by_type.values():
        if not grouped_profiles or any(profile.get("active") is True for profile in grouped_profiles):
            continue
        #3. 没有active-》 找第一个没有“active” 键的profile，设为active
        legacy_profiles_without_active=[profile for profile in grouped_profiles if "active" not in profile]
        if legacy_profiles_without_active:
            legacy_profiles_without_active[0]["active"]=True


class LLMConfig(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", strict=True, validate_assignment=True)

    profile_id: str | None = None
    provider:str
    service_zone: Literal["green", "yellow"] = "green"
    api_protocol: str = str(openai_api_protocol.OpenAIAPIProtocol.CHAT_COMPLETIONS)
    model_name: str = "gpt-4"
    base_url: str = ""
    api_key: str = ""
    his_app: str | None = None
    his_credential: str | None = None
    auth_url: str | None = None
    model_api_url: str | None = None
    use_proxy: bool = False
    http_proxy: str | None = None
    https_proxy: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    temperature: float = 0.7
    reasoning_effort: str | None = None
    service_tier: str | None = None
    max_tokens: int | None = None
    context_window_size: int | None = None
    metadata: dict[str, Any] | None = None
    thinking_mode: bool | str | None = None
    custom_headers: dict[str, str] | None = None

    @property
    def provider(self) -> str:
        """``AgentRuntimeModelConfig`` 契约里的 ``provider``。

        本类的存储字段叫 ``provider_values``，而运行时的模型配置协议按
        ``provider`` 取值（``AgentRuntime.llm_config`` 是 ``strict`` + 协议类型，
        缺一个属性就会整体校验失败），所以这里做一次名字对齐。
        """
        return self.provider

    @property
    def thinking_model(self) -> bool:
        """``AgentRuntimeModelConfig`` 契约里的 ``thinking_model``（``bool``）。

        对应本类的 ``thinking_mode``：外部配置里可能写成字符串（``"true"`` /
        ``"high"`` 之类），契约要求布尔，所以在这里收敛成 ``bool``。

        未配置（``None``）时返回 ``True`` 而不是 ``bool(None) == False``。
        三态语义里「没配」表示**沿用模型默认**，而 DeepSeek-V4 这类思考模型
        的默认恰恰是开启思考；一旦这里返回 ``False``，请求就会带上
        ``extra_body={"thinking": {"type": "disabled"}}``，实测 ModelScope 上的
        ``deepseek-ai/DeepSeek-V4.1-Flash-1017`` 收到该参数后会**完全不输出**
        （``content`` 与 ``reasoning_content`` 双双为空），TUI 于是屏上无字。

        字符串按常见真值词解析；无法识别的字符串视为 True（同「未配置」）。
        """
        value = self.thinking_mode
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in _FALSE_THINKING_LITERALS
        return bool(value)