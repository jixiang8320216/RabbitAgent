from __future__ import annotations

import typing

import pydantic

type JsonScalar = str|int |float|bool|None
type JsonFlatObject = dict[str,JsonScalar]
type JsonValue = JsonScalar|list[JsonScalar] |dict[str,JsonValue]
type JsonObject = dict[str,JsonValue]

_JSON_OBJECT_ADAPTER = pydantic.TypeAdapter(JsonObject)


def validate_json_object(value: object) -> JsonObject:
    return _JSON_OBJECT_ADAPTER.validate_python(value)


class LLMRuntimeConfiguration(pydantic.BaseModel):
    """一次 LLM 调用所需的运行时配置。

    由 ``LLMRuntimeConfigurationResolver`` 从 model profile 解析出来，交给
    provider adapter（例如 ``AnthropicProvider.__init__``）构造 ``ChatModel``。

    字段一律可空：配置缺失不该让解析/装配失败，而应该在真正发请求时暴露。
    额外字段保留（``extra="allow"``），避免 profile 里多出来的键在解析期被吞掉。
    """

    model_config = pydantic.ConfigDict(extra="allow")

    provider: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_protocol: str | None = None

    temperature: float | None = None
    max_tokens: int | None = None
    context_window: int | None = None
    max_input_tokens: int | None = None

    stream: bool | None = None

    use_proxy: bool | None = None
    http_proxy: str | None = None
    https_proxy: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    verify_ssl: bool | None = None

    custom_headers: dict[str, str] | None = None

    @property
    def effective_base_url(self) -> str | None:
        return (self.base_url or "").strip().rstrip("/") or None

    @classmethod
    def from_mapping(cls, data: object) -> LLMRuntimeConfiguration:
        """从任意 mapping 解析；不是 mapping 就当空配置。"""
        if not isinstance(data, typing.Mapping):
            return cls()
        return cls.model_validate(dict(data))


__all__ = [
    "JsonScalar",
    "JsonFlatObject",
    "JsonValue",
    "JsonObject",
    "validate_json_object",
    "LLMRuntimeConfiguration",
]