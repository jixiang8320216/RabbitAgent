"""OpenAI 兼容端点的 thinking / 推理请求参数解析与按模型族的策略路由。"""

from __future__ import annotations

import enum

import pydantic
import typing
import urllib.parse


from domains.llm.provider.domain import runtime_configuration
import domains.llm.provider.application.model.capability_resolution as model_capability_resolution

_RABBIT_LITELLM_HOST = "rabbit_cloudbu.huawei.com"
_DEEPSEEK_REASONING_EFFORT_MAP={
    "low":"high",
    "medium":"high",
    "high":"high",
    "xhigh":"high",
    "max":"max",
}

# 兼容旧调用点（protocol_strategy.py 等仍通过本模块访问该判定）。
is_deepseek_model = model_capability_resolution.is_deepseek_model

class OpenAICompatibleModeFamily(str, enum.Enum):
    DEFAULT = "default"
    OPENAI_REASONING="openai-reasoning"
    DEEPSEEK = "deepseeek"
    GLM="glm"
    QWEN="qwen"
    MINIMAX="minimax"
    KIMI="kimi"


class OpenAICompatibleRequestContext(pydantic.BaseModel):
    model_name: str
    base_url: str | None = None
    thinking_mode: bool | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    raw_extra_body: runtime_configuration.JsonObject | None = None
    include_standard_reasoning_effort: bool = False

class ThinkingPayloadeRoute(str, enum.Enum):
    DIRECT="openai-compatible"
    RABBIT_LITELLM="litellm double-wrap"


class OpenAICompatibleRequestOptions(pydantic.BaseModel):

    family: OpenAICompatibleModeFamily = OpenAICompatibleModeFamily.DEFAULT
    temperature: float | None = None
    extra_body: runtime_configuration.JsonObject | None = None
    thinking_mode: bool | None = None
    sdk_reasoning_effort: str | None = None
    payload_route: ThinkingPayloadeRoute= ThinkingPayloadeRoute.DIRECT


def resolve_temperature(
    configured_temperature: float | None,
    override: object | None,
    *,
    override_provided: bool,
) -> float | None:
    if not override_provided:
        return configured_temperature
    if override is None:
        return None
    if isinstance(override, (int, float)) and not isinstance(override, bool):
        return float(override)
    raise TypeError(f"'{type(override).__name__}' is an invalid type")

def is_rabbit_litellm_endpoint(base_url:str|None)->bool:
    if not base_url:
        return False
    try:
        return urllib.parse.urlparse(base_url).hostname==_RABBIT_LITELLM_HOST
    except ValueError:
        return False

def normalize_deepseek_reasoning_effort(reasoning_effort:str)->str:
    normalized= reasoning_effort.strip().lower()
    return _DEEPSEEK_REASONING_EFFORT_MAP.get(normalized, normalized)

def _validate_extra_body(
    raw_extra_body: runtime_configuration.JsonObject|None,
)->runtime_configuration.JsonObject:
    if raw_extra_body is None:
        return {}
    return runtime_configuration.validate_json_object(raw_extra_body)

def _build_routed_extra_body(
    context: OpenAICompatibleRequestContext,
    request_fields: runtime_configuration.JsonObject,
        *,
    outer_fields: runtime_configuration.JsonObject|None=None,
) -> tuple[runtime_configuration.JsonObject, ThinkingPayloadeRoute]:
    extra_body=_validate_extra_body(context.raw_extra_body)
    if outer_fields is not None:
        extra_body.update(outer_fields)
    if not is_rabbit_litellm_endpoint(context.base_url):
        extra_body.update(request_fields)
        return extra_body, ThinkingPayloadeRoute.DIRECT
    raw_nested_extra_body= extra_body.get("extra_body")
    nested_extra_body=(
        runtime_configuration.validate_json_object(raw_nested_extra_body)
        if isinstance(raw_nested_extra_body,dict)
        else {}
    )
    nested_extra_body.update(request_fields)
    extra_body["extra_body"]= nested_extra_body
    return extra_body, ThinkingPayloadeRoute.RABBIT_LITELLM

class _ModelRequestStrategy(typing.Protocol):
    def matches(self, context: OpenAICompatibleRequestContext) -> bool: ...
    def resolve(self, context: OpenAICompatibleRequestContext) -> OpenAICompatibleRequestOptions: ...


class _KimiRequestStrategy:
    def matches(self, context: OpenAICompatibleRequestContext) -> bool:
        return model_capability_resolution.is_kimi_k3_model(context.model_name)

    def resolve(self, context: OpenAICompatibleRequestContext) -> OpenAICompatibleRequestOptions:
        #todo
        return None


class _DeepSeekRequestStrategy:
    def matches(self, context: OpenAICompatibleRequestContext) -> bool:
        return model_capability_resolution.is_deepseek_model(context.model_name)

    def resolve(self, context: OpenAICompatibleRequestContext) -> OpenAICompatibleRequestOptions:
        thinking_enabled= context.thinking_mode is not False
        thinking_options:runtime_configuration.JsonObject={
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
        }
        outer_fields :runtime_configuration.JsonObject={}
        if context.reasoning_effort:
            outer_fields["reasoning_effort"]= normalize_deepseek_reasoning_effort(context.reasoning_effort)
        elif thinking_enabled and "reasoning_effort" not in _validate_extra_body(context.raw_extra_body):
            outer_fields["reasoning_effort"]="max"
        extra_body, payload_route= _build_routed_extra_body(
            context,
            thinking_options,
            outer_fields=outer_fields,
        )
        return OpenAICompatibleRequestOptions(
            family=OpenAICompatibleModeFamily.DEEPSEEK,
            temperature= None if thinking_enabled else context.temperature,
            extra_body= extra_body,
            thinking_enabled= thinking_enabled,
            payload_route=payload_route,
        )


class _GlmRequestStrategy:
    def matches(self, context: OpenAICompatibleRequestContext) -> bool:
        return model_capability_resolution.is_glm_thinking_model(context.model_name)

    def resolve(self, context: OpenAICompatibleRequestContext) -> OpenAICompatibleRequestOptions:
        normalized_effort:str|None=None
        if context.reasoning_effort and model_capability_resolution.is_glm_reasoning_effort(
            context.model_name
        ):
            normalized_effort= model_capability_resolution.normalize_glm_reasoning_effort(
                context.reasoning_effort
            )
        thinking_enabled= context.thinking_mode is not False and normalized_effort !="none"
        request_fields: runtime_configuration.JsonObject={
            "thinking":{
            "type":"enabled" if thinking_enabled else "disabled",
              "clear_thinking":False,
           }
        }
        if normalized_effort is not None:
            request_fields["reasoning_effort"]= normalized_effort
        extra_body, payload_route= _build_routed_extra_body(context, request_fields)
        temperature= context.temperature
        if temperature is None and thinking_enabled:
            thinking_enabled=1.0
        return OpenAICompatibleRequestOptions(
            family= OpenAICompatibleModeFamily.GLM,
            temperature= temperature,
            extra_body= extra_body,
            thinking_enabled= thinking_enabled,
            payload_route=payload_route,
        )


class _QwenRequestStrategy:
    def matches(self, context: OpenAICompatibleRequestContext) -> bool:
        return model_capability_resolution.is_qwen_thinking_model(context.model_name)

    def resolve(self, context: OpenAICompatibleRequestContext) -> OpenAICompatibleRequestOptions:
        capability= model_capability_resolution.get_qwen_thinking_capability(context.model_name)
        sdk_reasoning_effort=(
            context.reasoning_effort if context.include_standard_reasoning_effort else None
        )
        if capability is model_capability_resolution.QwenThinkingCapability.THINKING_ONLY:
            extra_body= _validate_extra_body(context.raw_extra_body)
            payload_route=(
                ThinkingPayloadeRoute.RABBIT_LITELLM
                if is_rabbit_litellm_endpoint(context.base_url)
                else ThinkingPayloadeRoute.DIRECT
            )
            return OpenAICompatibleRequestOptions(
                family= OpenAICompatibleModeFamily.QWEN,
                temperature= context.temperature,
                sdk_reasoning_effort= sdk_reasoning_effort,
                extra_body= extra_body or None,
                thinking_enabled= True,
                payload_route=payload_route,
            )
        default_enabled= model_capability_resolution.get_qwen_thinking_default(context.model_name)
        thinking_enabled=context.thinking_mode if context.thinking_mode is not None else default_enabled
        if thinking_enabled is None:
            raise RuntimeError("Qwen thinking is not enabled")
        request_fields: runtime_configuration.JsonObject={"enable_thinking":thinking_enabled}
        extra_body,payload_route= _build_routed_extra_body(context, request_fields)
        return OpenAICompatibleRequestOptions(
            family= OpenAICompatibleModeFamily.QWEN,
            temperature= context.temperature,
            sdk_reasoning_effort= sdk_reasoning_effort,
            extra_body= extra_body,
            thinking_enabled= thinking_enabled,
            payload_route=payload_route,
        )


class _MiniMaxRequestStrategy:
    def matches(self, context: OpenAICompatibleRequestContext) -> bool:
        return model_capability_resolution.is_minimax_thinking_model(context.model_name)

    def resolve(self, context: OpenAICompatibleRequestContext) -> OpenAICompatibleRequestOptions:
        capability= model_capability_resolution.get_minimax_thinking_capability(context.model_name)
        request_fields: runtime_configuration.JsonObject={"reasoning_split":True}
        thinking_enabled=True
        if capability is model_capability_resolution.MiniMaxThinkingCapability.HYBRID_DEFAULT_ENABLED:
            thinking_enabled=context.thinking_mode is not False
            request_fields["thinking"]={
                "type":"adaptive" if thinking_enabled else "disabled",
            }
        extra_body, payload_route = _build_routed_extra_body(context, request_fields)
        return OpenAICompatibleRequestOptions(
                family= OpenAICompatibleModeFamily.MINIMAX,
                temperature= context.temperature,
                extra_body= extra_body,
                thinking_enabled= thinking_enabled,
                payload_route=payload_route,
            )


class _DefaultRequestStrategy:
    def matches(self, context: OpenAICompatibleRequestContext) -> bool:
        return False

    def resolve(self, context: OpenAICompatibleRequestContext) -> OpenAICompatibleRequestOptions:
        is_o1_model ="o1"in context.model_name.lower()
        reasoning_effort=None
        if context.include_standard_reasoning_effort:
            reasoning_effort= context.reasoning_effort
            if reasoning_effort is None and is_o1_model:
                reasoning_effort= "high"
        return OpenAICompatibleRequestOptions(
            family= (
                OpenAICompatibleModeFamily.OPENAI_REASONING
                if is_o1_model
                else OpenAICompatibleModeFamily.DEFAULT
            ),
            temperature= None if is_o1_model else context.temperature,
            sdk_reasoning_effort= reasoning_effort,
        )


_MODEL_REQUEST_STRATEGIES: tuple[_ModelRequestStrategy, ...] = (
    _DeepSeekRequestStrategy(),
    _GlmRequestStrategy(),
    _QwenRequestStrategy(),
    _MiniMaxRequestStrategy(),
    _KimiRequestStrategy(),
)

_DEFAULT_REQUEST_STRATEGY = _DefaultRequestStrategy()


def resolve_openai_compatible_request(
    *,
    model_name: str,
    base_url: str | None,
    thinking_mode: bool | None,
    reasoning_effort: str | None,
    temperature: float | None,
    raw_extra_body: object | None = None,
    include_standard_reasoning_effort: bool = False,
) -> OpenAICompatibleRequestOptions:
    validated_extra_body = (
        runtime_configuration.validate_json_object(raw_extra_body)
        if raw_extra_body is not None
        else None
    )
    context = OpenAICompatibleRequestContext(
        model_name=model_name,
        base_url=base_url,
        thinking_mode=thinking_mode,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        raw_extra_body=validated_extra_body,
        include_standard_reasoning_effort=include_standard_reasoning_effort,
    )
    for strategy in _MODEL_REQUEST_STRATEGIES:
        if strategy.matches(context):
            return strategy.resolve(context)
    return _DEFAULT_REQUEST_STRATEGY.resolve(context)
