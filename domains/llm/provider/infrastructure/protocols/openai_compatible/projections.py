"""Transport projection helpers for the OpenAI-compatible protocol (stub).

This module is a *placeholder*: the runtime transport layer that these
symbols belong to has not been implemented yet. The names are provided so
that the import graph and type annotations resolve; the runtime helpers
raise ``NotImplementedError`` instead of doing real work. Replace this
with the real implementation when the transport layer lands.
"""

import typing

# ---------------------------------------------------------------------------
# Annotations / protocol bases (referenced at class-definition time)
# ---------------------------------------------------------------------------

ChatResponse = typing.Any
AuthHook = typing.Any
ModelIdentityResolver = typing.Any
_AsyncProviderCall = typing.Any


class _AsyncProviderClientPort(typing.Protocol):
    """Protocol stub for an async provider client port (unimplemented)."""


class _OpenAIStreamStreamPort(typing.Protocol):
    """Protocol stub for an OpenAI stream port (unimplemented)."""


class _OpenAIReasoningDetailsState:
    """Accumulated reasoning-detail state (unimplemented stub)."""


class _OpenAICumulativeTextState:
    """Accumulated text-content state (unimplemented stub)."""


class _OpenAIToolArgumentState:
    """Accumulated tool-argument state (unimplemented stub)."""


class _IncompleteOpenAIStreamError(Exception):
    """Raised when a stream ends without a terminal event (stub)."""


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------
# OpenAI 兼容侧的传输层此前是桩模块（见文件头注释）。实际可用的实现位于
# ``llm.provider.infrastructure.foundation.chat_tranport.contracts``（shared_contracts），
# anthropic 家族也是从这里取真实实现。这里统一委托过去，避免任何请求/收尾阶段
# 再抛 ``NotImplementedError``。
import domains.llm.provider.infrastructure.foundation.chat_transport.contracts as _shared_contracts


async def _close_provider_clients(clients: list, label: str) -> None:
    await _shared_contracts.close_provider_clients(clients, label)


async def _close_provider_client(client: object, label: str) -> None:
    await _shared_contracts.close_provider_client(client, label)


async def _close_provider_stream(stream: object, label: str) -> None:
    await _shared_contracts.closer_async_generator(stream, label)


def _openai_chunk_to_chat_response(
    chunk: object,
    *,
    reasoning_details_state: typing.Any = None,
    cumulative_content_state: typing.Any = None,
    tool_argument_state: typing.Any = None,
) -> object:
    """把一条 OpenAI 兼容的流式 chunk 转成内部 ``ChatResponse``。

    这曾是「非流式路径不走这里」的占位桩（恒返回 ``None``），而
    ``openai_compatible.stream`` 的流式路径**恰恰只调它**——于是每一个 chunk
    都被静默丢弃：上游 HTTP 200、正文也确实返回了
    （``delta.content`` 有值），但 ``ModelTurn.full_text`` 恒为 ``''``、
    ``content`` 恒为 ``[]``，TUI 屏上因此永远看不到模型答案。

    这里按消费方（``runtime.service.react_agent_streaming._ingest_block``）
    期望的形状构造：

    - ``delta.content``            -> :class:`TextBlock`
    - ``delta.reasoning_content``  -> :class:`ThinkingBlock`（DeepSeek 等思考模型）
    - ``delta.tool_calls[]``       -> :class:`ToolUseDeltaBlock`

    chunk 里没有可用内容时返回 ``None``，让调用方跳过——这与原先桩的行为一致，
    不改变「空 chunk 被忽略」的语义。
    """
    if chunk is None:
        return None

    choices = getattr(chunk, "choices", None)
    if not choices:
        # 末帧可能只带 usage，没有 choices。
        usage = _project_usage(getattr(chunk, "usage", None))
        finish_reason = None
        return _shared_contracts.ChatResponse(
            content=[],
            finish_reason=finish_reason,
            usage=usage,
        )

    choice = choices[0]
    delta = getattr(choice, "delta", None)
    if delta is None:
        return None

    blocks: list[typing.Any] = []

    reasoning = getattr(delta, "reasoning_content", None) or getattr(
        delta, "reasoning", None
    )
    if isinstance(reasoning, str) and reasoning:
        blocks.append(_thinking_block(reasoning))

    text = getattr(delta, "content", None)
    if isinstance(text, str) and text:
        blocks.append(_text_block(text))

    blocks.extend(_tool_delta_blocks(getattr(delta, "tool_calls", None)))

    finish_reason = getattr(choice, "finish_reason", None)
    usage = _project_usage(getattr(chunk, "usage", None))

    if not blocks and finish_reason is None and usage is None:
        # 纯心跳/空帧：没有任何可用信号。
        return None

    return _shared_contracts.ChatResponse(
        content=blocks,
        finish_reason=finish_reason,
        usage=usage,
    )


def _text_block(text: str) -> typing.Any:
    """构造正文块。``message`` 被延迟导入以避免模块级循环依赖。"""
    import domains.agent.domain.value_objects.message as message_module

    return message_module.TextBlock(type="text", text=text)


def _thinking_block(thinking: str) -> typing.Any:
    """构造思考块（DeepSeek ``reasoning_content`` 等）。"""
    import domains.agent.domain.value_objects.message as message_module

    return message_module.ThinkingBlock(type="thinking", thinking=thinking)


def _tool_delta_blocks(tool_calls: typing.Any) -> list[typing.Any]:
    """把 ``delta.tool_calls[]`` 转成 ``ToolUseDeltaBlock`` 列表。

    OpenAI 协议里工具参数是**增量**片段，因此统一标成 ``append``；
    ``index`` 原样透传，供``ToolCallStreamAccumulator`` 按序号归并。
    """
    if not tool_calls:
        return []
    import domains.agent.domain.value_objects.message as message_module

    blocks: list[typing.Any] = []
    for call in tool_calls:
        function = getattr(call, "function", None)
        arguments = getattr(function, "arguments", None) if function else None
        if not arguments:
            # 首帧通常只带 id/name，参数为空——跳过，等后续片段。
            continue
        if not isinstance(arguments, str):
            continue
        index = getattr(call, "index", None)
        blocks.append(
            message_module.ToolUseDeltaBlock(
                type="tool_use_delta",
                input_delta=arguments,
                id=getattr(call, "id", None),
                name=getattr(function, "name", None) if function else None,
                index=index if isinstance(index, (int, str)) else None,
                input_delta_kind="append",
            )
        )
    return blocks


def _project_usage(raw_usage: typing.Any) -> typing.Any:
    """把 provider 的 usage 对象转成内部 ``ChatUsage``；拿不到返回 ``None``。"""
    if raw_usage is None:
        return None
    input_tokens = getattr(raw_usage, "prompt_tokens", None)
    output_tokens = getattr(raw_usage, "completion_tokens", None)
    if input_tokens is None and output_tokens is None:
        return None
    details = getattr(raw_usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None) if details else None
    return _shared_contracts.ChatUsage(
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        reasoning_tokens=int(reasoning_tokens or 0),
        cached_tokens=int(getattr(raw_usage, "cached_tokens", None) or 0),
    )


def tool_call_observer_projection(block: object) -> object:
    return _shared_contracts.tool_call_observer_projection(block)


def log_observer_callback_failure(callback: object, agent_name: object, error: object) -> None:
    _shared_contracts.log_observer_callback_failure(callback, agent_name, error)


def _log_observer_callback_failure(callback: object, agent_name: object, error: object) -> None:
    _shared_contracts.log_observer_callback_failure(callback, agent_name, error)


def _log_obsever_callback_failure(callback: object, agent_name: object, error: object) -> None:
    _shared_contracts.log_observer_callback_failure(callback, agent_name, error)


def _provider_error_response(error: BaseException) -> object:
    return _shared_contracts.provider_error_response(error)
