import typing
import logging
import collections.abc
import collections.abc as abc
import pydantic

import domains.agent.domain.value_objects.message as message
import domains.agent.domain.value_objects.chat_response as chat_response
import domains.agent.domain.value_objects.tool_call as tool_call_model
import domains.agent.domain.aggregates.tool_call_stream as tool_call_stream

logger = logging.getLogger(__name__)

type TextDeltaEmitter= collections.abc.Callable[[str], typing.Awaitable[None]]
type ToolDeltaEmitter= collections.abc.Callable[[str,str,str,str,str|None,int|None,str|None], typing.Awaitable[None]]
type TextStreamEndNotifier= collections.abc.Callable[[], typing.Awaitable[None]]

class ModelTurn(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True, strict=True)
    content: list[message.ContentBlock]
    full_text: str
    full_thinking:str
    tool_call_errors: dict[str, tool_call_model.ToolInputError]
    finish_reason:str|None=None
    stop_details: chat_response.ChatStopDetails |None=None

    def truncated(self)->bool:
        return self.finish_reason in ("length","max_tokens")


class _StreamState:
    def __init__(self)->None:
        self.text:list[str]=[]
        self.thinking:list[str]=[]
        self.redacted_thinking:list[message.RedactedThinkingBlock]=[]
        self.tool_calls = tool_call_stream.ToolCallStreamAccumulator()
        self.finish_reason:str| None=None
        self.stop_details:chat_response.ChatStopDetails |None=None

async def collect_model_turn(
    stream: typing.AsyncGenerator[chat_response.ChatResponse, None],
    *,
    agent_name:str,
    emit_text_delta:TextDeltaEmitter,
    emit_tool_delta:ToolDeltaEmitter,
    notify_text_stream_end: TextStreamEndNotifier,
    event_logger: logging.Logger,
)->ModelTurn:
    state=_StreamState()
    try:
        async for chunk in stream:
            _capture_stop_details(state, chunk)
            await _ingest_content(
                state,
                chunk.content,
                agent_name=agent_name,
                emit_text_delta=emit_text_delta,
                emit_tool_delta=emit_tool_delta,
            )
    finally:
        await stream.aclose()
        if state.text:
            await notify_text_stream_end()
    return _resolve_turn(state, event_logger=event_logger, agent_name=agent_name)

def _capture_stop_details(state:_StreamState, chunk:chat_response.ChatResponse)->None:
    if chunk.finish_reason is not None:
        state.finish_reason = chunk.finish_reason
    if chunk.stop_details is not None:
        state.stop_details = chunk.stop_details

async def _ingest_content(
        state:_StreamState,
        content:str| list[message.StreamContentBlock],
        *,
        agent_name:str,
        emit_text_delta:TextDeltaEmitter,
        emit_tool_delta:ToolDeltaEmitter,
)->None:
    if not content:
        return
    if isinstance(content,str):
        state.text.append(content)
        await emit_text_delta(content)
        return
    for block in content:
        await _ingest_block(
            state,
            block,
            agent_name=agent_name,
            emit_text_delta=emit_text_delta,
            emit_tool_delta=emit_tool_delta,
        )

async def _ingest_block(
        state:_StreamState,
        block:message.StreamContentBlock,
        *,
        agent_name:str,
        emit_text_delta:TextDeltaEmitter,
        emit_tool_delta:ToolDeltaEmitter,
)->None:
    if isinstance(block,message.TextBlock):
        if block.text:
            state.text.append(block.text)
            await emit_text_delta(block.text)
        return
    if isinstance(block,message.ToolUseBlock):
        updates= state.tool_calls.ingest_complete(block)
        for update in updates:
            await _emit_tool_update(update,emit_tool_delta)
        return
    if isinstance(block, message.ToolUseDeltaBlock):
        update=state.tool_calls.ingest_delta(block)
        if isinstance(update,tuple):
            for item in update:
                await _emit_tool_update(item, emit_tool_delta)
        elif isinstance(update, tool_call_model.ToolDeltaRoutingFailure):
             logger.error(" failing all active tool deltas")
        return
    if isinstance(block, message.ThinkingBlock) and block.thinking:
        state.thinking.append(block.thinking)
    elif isinstance(block, message.RedactedThinkingBlock) :
        state.redacted_thinking.append(block)

async def _emit_tool_update(update:tool_call_model.ToolCallStreamUpdate, emit: ToolDeltaEmitter)->None:
    await emit(
        update.tool_id,
        update.tool_name,
        update.input_stream_phase,
        update.input_preview,
        update.input_fragment,
        update.input_fragment_index,
        update.input_fragment_kind,
    )

def _resolve_turn(
    state:_StreamState,
    *,
    event_logger:logging.Logger,
    agent_name:str,
)->ModelTurn:
    full_text="".join(state.text)
    full_thinking="".join(state.thinking)
    content:list[message.ContentBlock]=[]
    if full_thinking:
        content.append(message.ThinkingBlock(type="thinking", thinking=full_thinking))
    content.extend(state.redacted_thinking)
    if full_text:
        content.append(message.TextBlock(type="text", text=full_text))
    tool_call_errors:dict[str,tool_call_model.ToolInputError]={}
    for resolution in state.tool_calls.resolve():
        content.append(resolution.block)
        stream_stats = resolution.stream_stats
        if resolution.error is not None:
            tool_call_errors[resolution.block.id]=resolution.error
    return ModelTurn(
        content=content,
        full_text=full_text,
        full_thinking=full_thinking,
        tool_call_errors=tool_call_errors,
        finish_reason=state.finish_reason,
        stop_details=state.stop_details,
    )