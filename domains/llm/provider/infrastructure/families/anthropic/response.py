
from __future__ import annotations

import logging
import collections.abc as abc

import pydantic

import domains.llm.provider.infrastructure.families.anthropic.contracts as native_transport
import domains.agent.domain.value_objects.chat_response as chat_contracts
import domains.agent.domain.value_objects.message as message_module
import domains.llm.provider.application.token.usage_extraction as token_usage_extraction

_LOGGER = logging.getLogger(__name__)
StopReasonMap = abc.Mapping[str,str]

def map_stop_reason(
    stop_reason: str|None,
    stop_reason_map: StopReasonMap ,
) -> str | None:
    if not stop_reason:
        return None
    return stop_reason_map.get(stop_reason,stop_reason)


def stop_details_from_sdk(stop_details: object) -> chat_contracts.ChatStopDetails | None:
    """Anthropic 的 ``stop_details`` -> runtime 的 ``ChatStopDetails``。

    ``pause_turn`` / ``refusal`` 这类非正常结束会带上它，形如
    ``{"type": "pause_turn"}``。``ChatStopDetails`` 要求**至少有一个非空字段**
    （三个都空会触发它的 model_validator），所以全空时这里返回 ``None`` 而不是
    造一个空壳。
    """
    if stop_details is None:
        return None
    try:
        projected= native_transport._StopDetailsProjection.model_validate(stop_details)
    except pydantic.ValidationError:
        return None# noqa: BLE001
    if not (projected.type or projected.catagory or projected.explanation):
        return None

    return chat_contracts.ChatStopDetails(type=projected.type, category=projected.catagory, explanation=projected.explanation)


def event_to_chat_response(
    event: object,
    *,
    stop_reason_map: StopReasonMap,
) -> native_transport.ChatResponse | None:
    """把一个 Anthropic **流式事件**转成 ``ChatResponse``。

    返回 ``None`` 表示「这个事件没有实质产出」——``ping`` / ``message_stop`` /
    ``content_block_stop`` 这类纯控制事件，以及空文本增量都是这种情况。上层
    （``_prefetch_business_events``）正是靠这个返回值判断「第一个业务事件到没到」。

    事件形状见 :func:`_event_parts`。``message_start`` 这类带 usage 但没有内容的
    事件**会**返回对象（usage 要往上游报），只是内容为空，不会被当成业务产出。
    """
    projected=native_transport._AnthropicEventProjection.model_validate(event)
    match projected.type:
        case "content_block_start":
            return _content_block_start_response(projected)
        case "content_block_delta":
            return _content_block_delta_response(projected)
        case "message_delta":
            return _message_delta_response(projected, stop_reason_map)
        case "message_start":
            return _message_start_response(projected)
        case _:
            return None

def _content_block_start_response(
    event: native_transport._AnthropicEventProjection) -> native_transport.ChatResponse | None:
    block= event.content_block
    if block is None:
        return None
    match block.type:
        case "text":
            return _text_response(block.text)
        case "thinking":
            return _thinking_response(block.thinking)
        case "tool_use":
            return chat_contracts.ChatResponse(
                content=[
                    message_module.ToolUseDeltaBlock(
                        type="tool_use_delta",
                        id = block.id or "",
                        name= block.name or "",
                        input_delta="",
                        index=event.index,
                    )
                ]
            )
        case _:
            return  None

def _content_block_delta_response(
    event: native_transport._AnthropicEventProjection) -> native_transport.ChatResponse | None:
    delta= event.delta
    if delta is None:
        return None
    match delta.type:
        case "text_delta":
            return _text_response(delta.text)
        case "thinking_delta":
            return _thinking_response(delta.thinking)
        case "input_json_delta":
            return _tool_input_delta_response(delta.partial_json, event.index)
        case _:
            return  None


def _text_response(text:str|None) -> native_transport.ChatResponse | None:
    if not text:
        return None
    return chat_contracts.ChatResponse(content=[message_module.TextBlock(type="text", text=text)])

def _thinking_response(thinking:str|None) -> native_transport.ChatResponse | None:
    if not thinking:
        return None
    return chat_contracts.ChatResponse(content=[message_module.TextBlock(type="thinking", thinking=thinking)])

def _tool_input_delta_response(
    partial_json:str|None,
    index:int|None,
   ) -> native_transport.ChatResponse | None:
    if not partial_json:
        return None
    return chat_contracts.ChatResponse(content=[message_module.TextBlock(type="tool_user_delta", index=index, input_delta=partial_json)])

def _message_delta_response(
    event:native_transport._AnthropicEventProjection,
    stop_reason_map:StopReasonMap,
)->native_transport.ChatResponse|None:
    finish_reason=None
    stop_details = None
    if event.delta is not None:
        finish_reason= map_stop_reason(event.delta.stop_reason, stop_reason_map)
        stop_details - stop_details_from_sdk(event.delta.stop_details)
    usage = _usage_from_projection(event.usage)
    if not (finish_reason or stop_details or usage):
        return None
    return chat_contracts.ChatResponse(
        content=[],
        usage=usage,
        finish_reason=finish_reason,
        stop_details=stop_details,
    )


def _message_start_response(
    event: native_transport._AnthropicEventProjection,
) -> native_transport.ChatResponse | None:
    usage = _usage_from_projection(event.message.usage if event.message is not None else None)
    return chat_contracts.ChatResponse(
        content=[],
        usage=usage,
    ) if usage is not None else None

def _usage_from_projection(usage:object)->chat_contracts.ChatResponse | None:
    if usage is None:
        return None
    return token_usage_extraction.TokenExtractor.from_anthropic(usage)

def message_to_chat_response(
    raw_message: object,
    *,
    stop_reason_map: StopReasonMap,
) -> native_transport.ChatResponse:
    projected=native_transport._AnthropicEventProjection.model_validate(raw_message)
    content=[
        rabbit_block
        for block in projected.content
        if (rabbit_block := _message_content_block(block)) is not None
    ]
    return chat_contracts.ChatResponse(
        content= content,
        usage= _usage_from_projection(projected.usage),
        finish_reason= map_stop_reason(projected.stop_reason, stop_reason_map),
        stop_details= stop_details_from_sdk(projected.stop_details),
    )

def _message_content_block(
    block: native_transport._AnthropicContentBlockProjection,
)-> message_module.StreamContentBlock |None:
    match block.type:
        case "text"  if block.text:
            return message_module.TextBlock(type="text", text=block.text)
        case "thinking" if block.thinking:
            return message_module.ThinkingBlock(type="thinking", thinking=block.thinking)
        case "tool_use":
            return message_module.ToolUseBlock(
                type="tool_use",
                id=block.id or "",
                name=block.name or "",
                input=block.input or {},
            )
        case _:
            return None







