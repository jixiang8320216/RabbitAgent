
from __future__ import annotations

from enum import StrEnum

from domains.llm.provider.domain.openai_app_protocol import OpenAIAPIProtocol
from domains.configuration.domain.model.config_data import  RuntimeConfigModel,ConfigMapping
from pydantic import Field
from collections.abc import  Mapping
from domains.llm.provider import composition as provider_composition


class RuntimeLLMProfiled(StrEnum):
    ACP_OVERRIDE= "acp-override"
    LEGACY_DEFAULT = "legacy-default"


class RuntimeModelProfileOverride(RuntimeConfigModel):
    model: str = ""

    @classmethod
    def from_raw(cls, value: object) -> RuntimeModelProfileOverride | None:
        if value is None:
            return value
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.model_validate(value)
        return None

class RuntimeLLMProfile(RuntimeConfigModel):
    profile_id: str = Field(default="",alias="id")
    name: str|None= None
    model_type:str= Field(default="llm",alias="type")
    provider:str|None = None
    #service_zone
    api_protocol: OpenAIAPIProtocol = Field(default=OpenAIAPIProtocol.CHAT_COMPLETIONS,alias="protocol")
    model:str|None=None
    model_name:str|None=Field(default=None,alias="modelName")
    base_url:str|None=Field(default=None,alias="baseUrl")
    api_url:str|None=Field(default=None,alias="apiUrl")
    api_key:str|None=Field(default=None,alias="apiKey")
    auth_url:str|None=Field(default=None,alias="codemateAuthUrl")
    model_api_url:str|None=Field(default=None,alias="codemateModelApiUrl")
    use_proxy:bool | None= Field(default=False,alias="useProxy")
    http_proxy:str|None= Field(default=None,alias="httpProxy")
    https_proxy:str|None= Field(default=None,alias="httpsProxy")
    proxy_username:str|None= Field(default=None,alias="proxyUsername")
    proxy_password:str|None= Field(default=None,alias="proxyPassword")
    active:bool |None=None
    temperature:float|None=None
    max_tokens: int|None=Field(default=None,alias="maxTokens")
    context_window_size:int | None= Field(default=None,alias="contextWindowSize")
    reasoning_effort: str|None= Field(default=None,alias="reasoningEffort")
    service_tier: str|None= Field(default=None,alias="serviceTier")
    metadata:ConfigMapping|None=None
    # 三态：``None`` = 用户未配置 → 沿用模型自身默认；``True``/``False`` = 显式开关。
    # 原默认值是 ``False``，等于「只要没写 thinkingMode 就强制关闭思考」。
    # 对 DeepSeek-V4 这类默认开启思考的模型，这会让请求带上
    # ``extra_body={"thinking": {"type": "disabled"}}``，实测 ModelScope 上的
    # ``deepseek-ai/DeepSeek-V4.1-Flash-1017`` 收到后**完全不输出**
    # （content 与 reasoning_content 双双为空），表现为 TUI 屏上问答应答却无字。
    # 缺省必须是 ``None``，不能替用户做「关闭」的决定。
    thinking_mode: bool |None= Field(default=None,alias="thinkingMode")
    custom_headers:dict[str,str]|None= Field(default=None,alias="customHeaders")


    @classmethod
    def from_raw(cls,value:object)->RuntimeLLMProfile|None:
        if isinstance(value,cls):
            return value
        if isinstance(value,Mapping):
            return cls.model_validate(provider_composition.migrate_provider_profile(dict(value)))
        return None

    @classmethod
    def list_from_raw(cls, value:object)->list[RuntimeLLMProfile]:
        if not isinstance(value,list):
            return []
        profiles: list[RuntimeLLMProfile] = []
        for item in value:
            profile = cls.from_raw(item)
            if profile is not None:
                profiles.append(profile)
        return profiles

    def display_name(self)->str:
        return self.name or self.model_name or self.mode or self.profile_id

    def deactivated(self)->RuntimeLLMProfile:
        return self.model_copy(update={"active":False})