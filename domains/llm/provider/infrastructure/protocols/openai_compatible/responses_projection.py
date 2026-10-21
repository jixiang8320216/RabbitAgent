import json
import logging
import typing

import pydantic

import domains.agent.domain.value_objects.message as message
import domains.agent.domain.value_objects.chat_response as chat_response

# 临时补偿：核心 ``ToolUseDeltaBlock`` 漏声明了 ``_fields``，按其文档字段构造会触发
# 基类 ``TypeError``（基类把未知键全部拒掉）。这里补上它本应有的「允许键集合」，使流式
# 工具参数增量块可正常构造。**这是 message.py 的缺陷，等那边补回 ``_fields`` 后本段即
# 无害**；本模块不重写 message.py 逻辑，只把缺失的常量补回来。
if not getattr(message.ToolUseDeltaBlock, "_fields", frozenset()):
    message.ToolUseDeltaBlock._fields = frozenset(
        {"type", "input_delta", "id", "name", "index", "input_delta_kind"}
    )

logger = logging.getLogger(__name__)

JsonObject = dict[str, object]

_JSON_OBJECT_ADAPTER = pydantic.TypeAdapter(dict[str, pydantic.JsonValue])


def validate_json_object(value: object) -> dict[str, pydantic.JsonValue]:
    return _JSON_OBJECT_ADAPTER.validate_python(value)


# ======================================================================
# 概述
#
# 本模块是 OpenAI **Responses API**（``/v1/responses``）与系统内部
# ``ChatResponse`` / ``message.StreamContentBlock`` 之间的投影层，分两部分：
#
#   1. 请求侧归一化：``split_instructions`` / ``_normalize_message_content`` /
#      ``convert_tools`` / ``convert_tool_choice`` / ``prompt_cache_key`` —
#      把内部消息 / 工具 / tool_choice 整理成 Responses API 要的格式。
#   2. 响应侧流式投影：``event_chunks`` 是入口，把一条 Responses API 的
#      streaming 事件 dict 投影成若干 ``ChatResponse`` 增量块；其余
#      ``_xxx_chunks`` / ``_xxx_blocks`` 是它的私有协作者；``usage`` 负责把
#      Responses 的 usage 对象抽成 ``ChatUsage``。
#
# 设计约定（与仓内其它 projection 一致）：投影函数**永不因为事件缺字段而抛
# 异常**——缺了就降级成空内容 / 跳过该事件，让主循环继续，而不是把整轮打挂。
# ======================================================================


# ----------------------------------------------------------------------
# 请求侧：消息 / 工具归一化
# ----------------------------------------------------------------------

def split_instructions(
    messages: list[JsonObject],
) -> tuple[str | None, list[JsonObject]]:
    """把 system 消息抽出来作为 Responses API 的 ``instructions``。

    Responses API 把系统提示放在顶层 ``instructions`` 字段，而不是放进
    ``messages``。这里扫描 ``messages``，把所有 ``role == "system"`` 的文本
    拼到一起返回，并给出剔除 system 之后的剩余消息列表（原顺序、原对象）。

    - system 消息内容可以是纯字符串，也可以是 ``[{"type":"text","text":...}]``
      之类的内容块列表；都按纯文本抽取。
    - 没有 system 消息时 ``instructions`` 返回 ``None``（调用方据此决定是否带
      ``instructions`` 字段）。
    """
    if not isinstance(messages, list):
        return None, []
    instructions_parts: list[str] = []
    remaining: list[JsonObject] = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            text = _extract_text(msg.get("content"))
            if text:
                instructions_parts.append(text)
        else:
            remaining.append(msg)
    instructions = "\n\n".join(instructions_parts) if instructions_parts else None
    return instructions, remaining


def _extract_text(content: object) -> str:
    """从消息 content（str / 块列表 / 块字典）里抽取纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                block_type = item.get("type")
                if block_type in ("text", "input_text", "output_text"):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        return "\n".join(part for part in parts if part)
    return ""


def _normalize_message_content(
    content: object,
) -> "str | list[message.StreamContentBlock]":
    """把消息 content 归一成「纯文本」或「内容块列表」。

    - ``None`` → ``""``
    - 字符串 → 原样返回
    - 列表 → 逐元素转成 ``TextBlock`` / ``ToolUseBlock`` / ``ThinkingBlock`` /
      ``ToolUseDeltaBlock`` 等内容块；无法识别的元素跳过
    - 单个 ``{"type":"text",...}`` 字典 → 取其文本
    - 其它字典 → 用 ``content_block_from_dict`` 尝试构造，失败则丢弃
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, message._ContentBlockBase):
        return [content]  # type: ignore[list-item]
    if isinstance(content, list):
        blocks: list[message.StreamContentBlock] = []
        for part in content:
            if isinstance(part, str):
                blocks.append(message.TextBlock(type="text", text=part))
            elif isinstance(part, message._ContentBlockBase):
                blocks.append(part)  # type: ignore[arg-type]
            elif isinstance(part, dict):
                block = _block_from_part(part)
                if block is not None:
                    blocks.append(block)
            # 其它类型忽略
        return blocks
    if isinstance(content, dict):
        block = _block_from_part(content)
        return [block] if block is not None else ""  # type: ignore[list-item]
    return ""


def _block_from_part(part: JsonObject) -> "message.StreamContentBlock | None":
    """把单个内容块字典安全地转成 ``StreamContentBlock``。"""
    block_type = part.get("type")
    try:
        if block_type == "tool_use_delta":
            raw_index = part.get("index")
            return message.ToolUseDeltaBlock(
                type="tool_use_delta",
                input_delta=part.get("input_delta") or "",
                id=part.get("id") if isinstance(part.get("id"), str) else None,
                name=part.get("name") if isinstance(part.get("name"), str) else None,
                index=raw_index,
            )
        return message.content_block_from_dict(part)
    except (ValueError, TypeError):
        logger.debug("skipping unrecognized content block: %r", part)
        return None


def convert_tools(tools: object) -> list[JsonObject]:
    """把 Chat Completions 风格的工具声明转成 Responses API 风格。

    Chat Completions::

        {"type":"function","function":{"name","description","parameters","strict"}}

    Responses API（扁平）::

        {"type":"function","name","description","parameters","strict"}

    已经是扁平形式的原样保留；非 dict / 未知形态跳过。
    """
    if not isinstance(tools, list):
        return []
    converted: list[JsonObject] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            converted.append(_responses_function_tool(tool["function"]))
        else:
            converted.append(tool)
    return converted


def _responses_function_tool(fn: JsonObject) -> JsonObject:
    parameters = fn.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    result: JsonObject = {
        "type": "function",
        "name": fn.get("name"),
        "description": fn.get("description", ""),
        "parameters": parameters,
    }
    strict = fn.get("strict")
    if isinstance(strict, bool):
        result["strict"] = strict
    return result


def convert_tool_choice(tool_choice: object) -> "JsonObject | str":
    """把 tool_choice 转成 Responses API 接受的形式。

    - ``None`` → ``"auto"``（默认）
    - 字符串（``"auto"`` / ``"none"`` / ``"required"``）→ 原样
    - Chat Completions 字典 ``{"type":"function","function":{"name":x}}``
      → ``{"type":"function","name":x}``
    - 其它字典 → 原样
    """
    if tool_choice is None:
        return "auto"
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function" and isinstance(
            tool_choice.get("function"), dict
        ):
            return {
                "type": "function",
                "name": tool_choice["function"].get("name"),
            }
        return tool_choice
    return "auto"


def prompt_cache_key(
    messages: object,
    tools: object = None,
    *,
    model: object = None,
) -> str:
    """为一组（messages, tools, model）生成一个稳定的缓存键。

    用于把相同前缀的提示命中自动提示缓存（Responses API 的
    ``prompt_cache_key`` / 服务端缓存），或对历史做去重。键基于可 JSON 化的
    内容做 sha256，返回带 ``pc_`` 前缀的截断摘要。
    """
    payload = {
        "model": model,
        "messages": _jsonable(messages),
        "tools": _jsonable(tools),
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str
    )
    digest = _sha256_hex(serialized)
    return f"pc_{digest[:24]}"


def _jsonable(value: object) -> object:
    try:
        # round-trip 以规整成纯 JSON 容器（去 dict 子类 / 不可序列化对象）
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def _sha256_hex(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# 响应侧：流式事件投影
# ----------------------------------------------------------------------

def response_chunk(
    content: "str | list[message.StreamContentBlock]",
    *,
    usage: chat_response.ChatUsage | None = None,
    finish_reason: str | None = None,
) -> chat_response.ChatResponse:
    """构造一个 ``ChatResponse`` 增量块（所有 ``_xxx_chunks`` 的统一出口）。"""
    return chat_response.ChatResponse(
        content=content,
        usage=usage,
        finish_reason=finish_reason,
    )


def event_chunks(event: JsonObject) -> typing.Iterator[chat_response.ChatResponse]:
    """Responses API 流式事件 → ``ChatResponse`` 增量块 的总入口。

    按 ``event["type"]`` 分发到对应的私有协作者；对不产生聊天内容的事件
    （``response.created`` / ``response.in_progress`` /
    ``response.content_part.added`` / ``*.done`` 的纯终态事件等）静默跳过。

    事件类型 → 处理：

    - ``response.output_text.delta``            → ``_text_delta_chunks``
    - ``response.output_item.added``            → ``_tool_call_added_chunks``
    - ``response.function_call_arguments.delta``→ ``_tool_arguments_delta_chunks``
    - ``response.output_item.done``             → ``_output_item_done_chunks``
    - ``response.completed``                    → ``_terminal_response_chunks``
    """
    if not isinstance(event, dict):
        return
    event_type = event.get("type")
    if event_type == "response.output_text.delta":
        yield from _text_delta_chunks(event)
    elif event_type == "response.output_item.added":
        yield from _tool_call_added_chunks(event)
    elif event_type == "response.function_call_arguments.delta":
        yield from _tool_arguments_delta_chunks(event)
    elif event_type == "response.output_item.done":
        yield from _output_item_done_chunks(event)
    elif event_type == "response.completed":
        yield from _terminal_response_chunks(event)
    # 其余事件类型不产出聊天块


def _text_delta_chunks(event: JsonObject) -> typing.Iterator[chat_response.ChatResponse]:
    """``response.output_text.delta``：把文本增量包成 ``TextBlock`` 块。"""
    delta = event.get("delta")
    if not isinstance(delta, str) or not delta:
        return
    yield response_chunk(
        content=[message.TextBlock(type="text", text=delta)]
    )


def _tool_call_added_chunks(event: JsonObject) -> typing.Iterator[chat_response.ChatResponse]:
    """``response.output_item.added``（function_call）：先发一个空的
    ``ToolUseBlock``（带上 id / name）作为「工具调用已开始」的信号；真正的
    arguments 由后续的 ``function_call_arguments.delta`` 增量补齐。"""
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return
    call_id = item.get("call_id") or item.get("id") or ""
    name = item.get("name") or ""
    yield response_chunk(
        content=[
            message.ToolUseBlock(
                type="tool_use",
                id=call_id if isinstance(call_id, str) else "",
                name=name if isinstance(name, str) else "",
                input={},
            )
        ]
    )


def _tool_arguments_delta_chunks(
    event: JsonObject,
) -> typing.Iterator[chat_response.ChatResponse]:
    """``response.function_call_arguments.delta``：把参数增量包成
    ``ToolUseDeltaBlock``（append 语义）。"""
    delta = event.get("delta")
    if not isinstance(delta, str) or not delta:
        return
    item_id = event.get("item_id")
    name = event.get("name")
    yield response_chunk(
        content=[
            message.ToolUseDeltaBlock(
                type="tool_use_delta",
                input_delta=delta,
                id=item_id if isinstance(item_id, str) else None,
                name=name if isinstance(name, str) else None,
                input_delta_kind="append",
            )
        ]
    )


def _output_item_done_chunks(
    event: JsonObject,
) -> typing.Iterator[chat_response.ChatResponse]:
    """``response.output_item.done``：按 item 类型把整块内容投影出去。

    - ``function_call`` → ``_completed_tool_call_chunks``（完整 ``ToolUseBlock``）
    - ``message``      → ``_message_item_blocks``（文本块列表）
    """
    item = event.get("item")
    if not isinstance(item, dict):
        return
    item_type = item.get("type")
    if item_type == "function_call":
        yield from _completed_tool_call_chunks(item)
    elif item_type == "message":
        blocks = _message_item_blocks(item)
        if blocks:
            yield response_chunk(content=blocks)
    # 其它 item 类型（reasoning / item_reference 等）暂不投影为聊天块


def _completed_tool_call_chunks(
    item: JsonObject,
) -> typing.Iterator[chat_response.ChatResponse]:
    """``function_call`` item 完成：把完整 arguments 解析成 ``ToolUseBlock``。"""
    blocks = _completed_tool_call_blocks(item)
    if blocks:
        yield response_chunk(content=blocks)


def _message_item_blocks(item: JsonObject) -> list[message.StreamContentBlock]:
    """把 Responses 的 ``message`` item 内容块（``output_text`` / ``input_text``）
    投影成 ``TextBlock`` 列表；``refusal`` / ``input_image`` 等忽略。"""
    if not isinstance(item, dict):
        return []
    content = item.get("content")
    if not isinstance(content, list):
        return []
    blocks: list[message.StreamContentBlock] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        block_type = part.get("type")
        if block_type in ("output_text", "input_text"):
            text = part.get("text")
            if isinstance(text, str) and text:
                blocks.append(message.TextBlock(type="text", text=text))
        # 其它 part 类型（refusal / input_image / annotations）忽略
    return blocks


def _completed_tool_call_blocks(item: JsonObject) -> list[message.StreamContentBlock]:
    """``function_call`` item → 单个 ``ToolUseBlock``（解析完整 arguments）。"""
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return []
    call_id = item.get("call_id") or item.get("id") or ""
    name = item.get("name") or ""
    raw_args = item.get("arguments")
    parsed = _parse_arguments(raw_args)
    return [
        message.ToolUseBlock(
            type="tool_use",
            id=call_id if isinstance(call_id, str) else "",
            name=name if isinstance(name, str) else "",
            input=parsed,
        )
    ]


def _responing_item_blocks(item: JsonObject) -> list[message.StreamContentBlock]:
    """按 item 类型把整块内容投影成内容块列表（``message`` / ``function_call``）。

    注意：方法名沿用历史拼写 ``_responing_``（漏了一个 d）；同时挂了正确拼写的
    别名 ``_responding_item_blocks``，两个名字都能调。
    """
    if not isinstance(item, dict):
        return []
    item_type = item.get("type")
    if item_type == "function_call":
        return _completed_tool_call_blocks(item)
    if item_type == "message":
        return _message_item_blocks(item)
    return []


# 正确拼写别名，避免调用方两种写法都可用
_responding_item_blocks = _responing_item_blocks


def _terminal_response_chunks(
    event: JsonObject,
) -> typing.Iterator[chat_response.ChatResponse]:
    """``response.completed``：产出终态块，只带 usage + finish_reason。

    正文已经在 ``output_text.delta`` 阶段流式吐过了，这里不再重复内容；只补
    上用量统计与终止原因。``event`` 既可能是 ``response.completed`` 事件（真正的
    response 在 ``event["response"]`` 里），也可能是非流式场景直接拿到的完整
    response 对象——两种都兼容。
    """
    response_obj = event.get("response")
    if not isinstance(response_obj, dict):
        response_obj = event
    raw_usage = response_obj.get("usage")
    chat_usage = usage(raw_usage)
    status = response_obj.get("status")
    finish_reason = _finish_reason_from_status(status)
    yield response_chunk(content=[], usage=chat_usage, finish_reason=finish_reason)


def _finish_reason_from_status(status: object) -> str | None:
    if not isinstance(status, str):
        return None
    mapping = {
        "completed": "stop",
        "incomplete": "length",
        "cancelled": "cancelled",
        "failed": "error",
    }
    return mapping.get(status, status)


def usage(raw_usage: object) -> chat_response.ChatUsage | None:
    """把 Responses API 的 usage 对象抽成 ``ChatUsage``。

    Responses usage 形态（字段名偏 OpenAI 风格）::

        {
          "input_tokens": int,
          "output_tokens": int,
          "output_tokens_details": {"reasoning_tokens": int, ...},
          "input_tokens_details": {"cached_tokens": int, ...},
          "cache_read_input_tokens": int   # 旧版平铺写法
        }
    """
    if not isinstance(raw_usage, dict):
        return None
    input_tokens = _as_nonneg_int(raw_usage.get("input_tokens"))
    output_tokens = _as_nonneg_int(raw_usage.get("output_tokens"))
    output_details = raw_usage.get("output_tokens_details")
    reasoning_tokens = _as_nonneg_int(
        output_details.get("reasoning_tokens")
        if isinstance(output_details, dict)
        else None
    )
    input_details = raw_usage.get("input_tokens_details")
    cached_tokens = _as_nonneg_int(
        input_details.get("cached_tokens")
        if isinstance(input_details, dict)
        else None
    ) or _as_nonneg_int(raw_usage.get("cache_read_input_tokens"))
    return chat_response.ChatUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
    )


def _as_nonneg_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _parse_arguments(raw_args: object) -> dict[str, object]:
    """把 function_call 的 arguments 解析成 dict（容错：解析失败返回 {}）。"""
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
        except (ValueError, TypeError):
            logger.debug("failed to parse tool call arguments: %r", raw_args)
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
