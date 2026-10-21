"""从「配置来源」解析出一份 ``LLMRuntimeConfiguration``。

``LLMModelAccessService`` 拿到的 ``configuration_source`` 是应用侧的
``ConfigManager``（见 ``app.infrastructure.config.runtime_configuration``），
它只提供 ``get`` / ``as_dict`` / ``get_property`` 这类**通用取值**接口，并不
知道 LLM 配置的形状。这一层负责把通用取值翻译成 provider adapter 认得的
``LLMRuntimeConfiguration``。

解析策略（宽松、逐级降级）：

1. 来源自己就提供了 LLM 配置（``resolve_llm_runtime_configuration`` /
   ``llm_runtime_configuration`` 属性）→ 直接用；
2. 从常用顶层键（``llm`` / ``model`` / ``models``）里取 mapping；
3. 都没有 → 拿整份配置的浅拷贝碰运气（``LLMRuntimeConfiguration`` 会忽略
   不认识的键，缺字段全为 ``None``）。

任何一步失败都不抛异常：配置缺失要在**真正发请求**时报错，而不是在解析期
让装配崩掉。
"""

from __future__ import annotations

import typing

import domains.llm.provider.domain.runtime_configuration as runtime_configuration

JsonObject = dict[str, typing.Any]

_CANDIDATE_KEYS: tuple[str, ...] = (
    "llm",
    "llm_runtime",
    "llm_runtime_configuration",
    "model",
    "model_profile",
    "models",
)


@typing.runtime_checkable
class ConfigurationSource(typing.Protocol):
    """配置来源协议。

    ``ConfigManager`` 天然满足（``reload_config`` + ``get`` / ``as_dict``）；
    只要求「能重新加载」和「能按键取值」两件事，便于测试里塞 dict 或桩对象。
    """

    def reload_config(self) -> None:
        ...

    def get(self, key: str, default: typing.Any = None) -> typing.Any:
        ...


def _as_mapping(value: object) -> JsonObject | None:
    return dict(value) if isinstance(value, typing.Mapping) else None


def extract_llm_configuration(source: object) -> JsonObject:
    """从任意配置来源里捞出 LLM 配置段。"""
    if source is None:
        return {}

    # 1) 来源自带解析结果
    resolver = getattr(source, "resolve_llm_runtime_configuration", None)
    if callable(resolver):
        try:
            own = _as_mapping(resolver())
        except TypeError:
            own = None
        if own:
            return own

    own_attr = getattr(source, "llm_runtime_configuration", None)
    own_attr = _as_mapping(own_attr)
    if own_attr:
        return own_attr

    # 2) 常用顶层键
    getter = getattr(source, "get", None)
    if callable(getter):
        for key in _CANDIDATE_KEYS:
            candidate = _as_mapping(getter(key))
            if candidate:
                return candidate

    # 3) 整份配置
    snapshot = getattr(source, "as_dict", None)
    if callable(snapshot):
        return _as_mapping(snapshot()) or {}
    return _as_mapping(source) or {}


class LLMRuntimeConfigurationResolver:
    """把配置来源解析成 ``LLMRuntimeConfiguration``。

    结果按 ``model_name`` 缓存——同一轮运行里配置来源（``ConfigManager``）
    的内容不会变，重复解析只会重复付 pydantic 校验的成本。
    """

    def __init__(self, configuration_source: ConfigurationSource | None) -> None:
        self._configuration_source = configuration_source
        self._cache: dict[str | None, runtime_configuration.LLMRuntimeConfiguration] = {}

    def resolve(
        self,
        model_name: str | None = None,
        *,
        provider: str | None = None,
    ) -> runtime_configuration.LLMRuntimeConfiguration:
        cache_key = model_name
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        raw = extract_llm_configuration(self._configuration_source)
        if model_name:
            raw = dict(raw)
            raw.setdefault("model_name", model_name)
        if provider:
            raw = dict(raw)
            raw.setdefault("provider", provider)

        try:
            resolved = runtime_configuration.LLMRuntimeConfiguration.model_validate(raw)
        except Exception:  # noqa: BLE001 - 配置形状不认识不该让装配失败
            resolved = runtime_configuration.LLMRuntimeConfiguration()

        self._cache[cache_key] = resolved
        return resolved

    def invalidate(self) -> None:
        self._cache.clear()


__all__ = [
    "ConfigurationSource",
    "LLMRuntimeConfigurationResolver",
    "extract_llm_configuration",
]
