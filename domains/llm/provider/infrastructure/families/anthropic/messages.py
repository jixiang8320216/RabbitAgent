
from __future__ import annotations

import json
import logging
import typing

import domains.agent.domain.aggregates.chat_model as chat_model
import domains.llm.provider.infrastructure.families.anthropic.contracts as native_transport

_LOGGER = logging.getLogger(__name__)

#: 系统提示各段落之间的分隔符（和 CLI 侧拼装时用的是同一个）。
DEFAULT_SYSTEM_SEPARATOR = "\n\n---\n\n"

#: Anthropic 一次请求最多允许 4 个 cache breakpoint，超了会 400。
MAX_CACHE_BREAKPOINTS = 4

#: ``thinking`` / ``redacted_thinking`` 永远不能当缓存点：它们带签名，
#: 每次请求内容都可能变，打上去只会让前缀缓存永远 miss。
_ALWAYS_NON_CACHEABLE_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

#: Anthropic 拒绝空文本块，内容被判定为「空」时用这个占位。
_PLACEHOLDER_TEXT = " "

#: 会被归到系统提示里的角色（OpenAI 的 ``developer`` 也算系统口径）。
_SYSTEM_ROLES = frozenset({"system", "developer"})

#: 会被归到 assistant 轮次的角色。
_ASSISTANT_ROLES = frozenset({"assistant", "model", "ai"})

#: 工具执行结果消息的角色。
_TOOL_ROLES = frozenset({"tool", "function", "tool_result"})

#: 需要排在 assistant 内容最前面的块类型（Anthropic 要求 thinking 在先）。
_THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

#: 工具定义里允许原样透传的 ``type`` 取值（OpenAI 的 ``"function"`` 不能透传，
#: 写进 Anthropic 的 tools 会被当成未知类型拒掉）。
_PASSTHROUGH_TOOL_TYPES = frozenset({"custom"})


# ----------------------------------------------------------------------
# 通用小工具
# ----------------------------------------------------------------------


def _as_dict(value: object) -> dict[str, object] | None:
    """是 dict 就还回去，否则 ``None``——省得到处写 ``isinstance``。"""
    return value if isinstance(value, dict) else None


def _as_sequence(value: object) -> list[object]:
    """把入参当列表看：非 list/tuple 一律当成空。"""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _first_str(mapping: object, *keys: str) -> str | None:
    """按顺序取第一个「非空字符串」字段。

    数字类型的 id 也认（有些调用方把 ``tool_call_id`` 写成整数）。
    """
    record = _as_dict(mapping)
    if record is None:
        return None
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _safe_json_object(raw: object) -> dict[str, object]:
    """把 JSON 字符串/字典统一成字典。

    工具参数是模型吐出来的，字符串里塞了非法 JSON 是家常便饭——这里解析失败
    就退成 ``{}``，**不能让一次参数解析失败炸掉整个请求**。
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _block_text(block: object) -> str:
    """从一个内容块里掏出可展示的文本（多轮对话里用于降维）。"""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        for key in ("text", "content", "thinking", "summary"):
            value = block.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
    if block is None:
        return ""
    return str(block)


def _content_to_text(content: object) -> str:
    """把任意形状的 ``content`` 摊平成一个字符串。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _block_text(content)
    if isinstance(content, list):
        return "\n".join(part for part in (_block_text(item) for item in content) if part)
    return str(content)


def _raw_blocks(content: object) -> list[object]:
    """把 ``content`` 统一成「块列表」，方便后面逐块转换。"""
    if content is None:
        return []
    if isinstance(content, list):
        return list(content)
    return [content]


# ----------------------------------------------------------------------
# 工具定义转换
# ----------------------------------------------------------------------


def _normalize_input_schema(schema: dict[str, object]) -> dict[str, object]:
    """补全 JSON Schema 的最低要求：``type`` 与 ``properties``。

    Anthropic 对 ``input_schema`` 的要求比 OpenAI 严——没有 ``properties``
    的空 schema 会被拒，所以这里一律补成 ``{}``。
    """
    normalized = dict(schema)
    if not _first_str(normalized, "type"):
        normalized["type"] = "object"
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


def _convert_tool(tool: chat_model.JsonObject) -> native_transport.ProviderMessage | None:
    """单条工具定义 -> Anthropic 形状，认不出来就返回 ``None`` 跳过。"""
    if tool.get("type") == "function":
        function = tool.get("function")
        if not isinstance(function,dict):
            return None
        return {
        "name": function.get("name",""),
        "description": function.get("description",""),
        "input_schema": function.get("parameters",{"type":"object","properties":{}}),
        }
    return dict(tool) if "name" in tool else None


def convert_tools(
    tools: list[chat_model.JsonObject] ,
) -> list[native_transport.ProviderMessage]:
    anthropic_tools: list[native_transport.ProviderMessage]=[]
    for tool in tools:
        projected = _convert_tool(tool)
        if projected is not None:
            anthropic_tools.append(projected)
    return anthropic_tools







# ----------------------------------------------------------------------
# 内容块转换
# ----------------------------------------------------------------------


def _image_block(record: dict[str, object]) -> native_transport.ProviderMessage | None:
    """OpenAI 的 ``image_url`` 块 -> Anthropic 的 ``image`` 块。

    支持 ``data:image/png;base64,xxx`` 内联图片和远程 URL 两种写法。
    """
    holder = _as_dict(record.get("image_url")) or _as_dict(record.get("image")) or {}
    raw_url = _first_str(holder, "url") or _first_str(record, "url") or _first_str(record, "data")
    if not raw_url:
        return None
    if raw_url.startswith("data:"):
        header, _, payload = raw_url.partition(",")
        media_type = header[len("data:"):].split(";")[0].strip() or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": payload},
        }
    return {"type": "image", "source": {"type": "url", "url": raw_url}}


def _native_content_block(block: object) -> native_transport.ProviderMessage | None:
    """把一个 runtime 侧内容块转成 Anthropic 块，空块返回 ``None``。

    已经是 Anthropic 原生类型（``thinking`` / ``tool_use`` /
    ``tool_result`` / 未知但有 ``type`` 的块）一律原样透传。
    """
    if isinstance(block, str):
        text = block.strip()
        return {"type": "text", "text": text} if text else None

    record = _as_dict(block)
    if record is None:
        text = str(block).strip()
        return {"type": "text", "text": text} if text else None

    block_type = str(record.get("type") or "").strip().lower()

    if block_type in _THINKING_BLOCK_TYPES:
        if block_type == "thinking":
            converted: native_transport.ProviderMessage = {
                "type": "thinking",
                "thinking": str(record.get("thinking") or ""),
            }
            signature = _first_str(record, "signature")
            if signature:
                converted["signature"] = signature
            return converted
        return {"type": "redacted_thinking", "data": str(record.get("data") or "")}

    if block_type == "text":
        text = _first_str(record, "text")
        if text is None:
            text = _content_to_text(record.get("content")).strip()
        return {"type": "text", "text": text} if text else None

    if block_type in ("image_url", "image"):
        return _image_block(record)

    if block_type in ("tool_use", "tool_result"):
        # 已经是原生形状，原样透传（id 缺失时补一个空串，避免后端校验失败）。
        passthrough = dict(record)
        passthrough["type"] = block_type
        return passthrough

    if block_type:
        # 未知的原生块（比如未来的类型）：原样透传，比擅自丢掉安全。
        return dict(record)

    text = _content_to_text(record).strip()
    return {"type": "text", "text": text} if text else None


def _tool_use_blocks(tool_calls: object) -> list[native_transport.ProviderMessage]:
    """assistant 消息上的 ``tool_calls`` -> ``tool_use`` 块列表。"""
    if isinstance(tool_calls, list):
        return []
    blocks: list[native_transport.ProviderMessage] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        function_data = function if isinstance(function, dict) else {}
        tool_id = tool_call.get("id")
        name= function_data.get("name")
        blocks.append(
            {
                "type": "tool_use",
                "id": tool_id if isinstance(tool_id,str) else {},
                "name": name if isinstance(name,str) else "",
                "input": _tool_input(function_data.get("arguments","")),
            }
        )
    return blocks

def _tool_input(arguments:object)->native_transport.ProviderMessage:
    try:
        parsed= json.loads(arguments) if isinstance(arguments, str) and arguments else arguments
    except (json.JSONDecodeError, TypeError):
        parsed = arguments
    return parsed if isinstance(parsed, dict) else {}

def _merge_consecutive_messages(
    messages: list[native_transport.ProviderMessage],
)->list[native_transport.ProviderMessage]:
    merged: list[native_transport.ProviderMessage] = []
    for message in messages:
        if not merged or merged[-1]["role"] != message["role"]:
            merged.append(message)
            continue
        merged[-1]["content"] = _content_blocks(merged[-1]["content"])+_content_blocks(message["content"])
    return merged

def _content_blocks(content: object)->list[object]:
    if isinstance(content, str):
        return[{"type":"text", "text": content}]
    if isinstance(content, list):
        return list(content)
    return []


def mark_last_message_cacheable(
    api_messages: list[native_transport.ProviderMessage] | None,
    *,
    excluded_block_types: frozenset[str] ,
) -> None:
    if not api_messages:
        return
    last_message = api_messages[-1]
    content = last_message.get("content")
    if isinstance(content, str):
        last_message["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
        return
    if not isinstance(content,list) or not content:
        return
    for block in reversed(content):
        if not isinstance(block, dict) or block.get("type") in excluded_block_types:
            continue
        if  "cache_control" not in block:
            block["cache_control"]= {"type": "ephemeral"}
            return

def split_system_into_blocks(
    system_text: str | None,
    *,
    separator: str,
    dynamic_markers: tuple[str,...] ,
) -> list[native_transport.ProviderMessage]:
    if not system_text:
        return []
    sections= system_text.split(separator)
    if len(sections) < 1:
        return [_cached_text_block(system_text)]
    startic_parts, dynamic_parts= _classify_system_sections(sections, dynamic_markers)
    blocks: list[native_transport.ProviderMessage] = []
    if startic_parts:
        blocks.append(_cached_text_block(separator.json(startic_parts)))
    if dynamic_parts:
        blocks.append({"type":"text", "text": separator.join(dynamic_parts)})
    return blocks or [_cached_text_block(system_text)]

def _classify_system_sections(
    sections: list[str],
    dynamic_markers: tuple[str,...] ,
)->tuple[list[str], list[str]]:
    static_parts: list[str] = []
    dynamic_parts: list[str] = []
    for section in sections:
        is_dynamic= any(section.strip().startswith(marker) for marker in dynamic_markers)
        if is_dynamic or dynamic_parts:
            dynamic_parts.append(section)
        else:
            static_parts.append(section)
    return static_parts, dynamic_parts

def _cached_text_block(text:str)->native_transport.ProviderMessage:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}

def stablize_tool_order(
    tools:list[native_transport.ProviderMessage]|None,
)->list[native_transport.ProviderMessage]|None:
    if not tools:
        return tools
    return sorted(tools, key=_tool_sort_key)

def _tool_sort_key(tool: object) -> str:
    direct_name= tool.get("name")
    if isinstance(direct_name, str):
        return direct_name
    function=tool.get("function")
    function_name=function.get("name") if isinstance(function,dict) else None
    return function_name if isinstance(function_name, str) else ""

def _tool_result_content(content: object) -> object:
    """工具结果的内容：能压成字符串就压，多个块就保留块列表。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        record = content
        if str(record.get("type") or "") == "tool_result":
            inner = record.get("content")
            if inner is not None:
                return _tool_result_content(inner)
        return _content_to_text(record)
    blocks: list[native_transport.ProviderMessage] = []
    for block in _raw_blocks(content):
        if isinstance(block, dict) and str(block.get("type") or "") == "text":
            text = _first_str(block, "text")
            if text is None:
                text = _content_to_text(block.get("content")).strip()
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        text = _content_to_text(block).strip()
        if text:
            blocks.append({"type": "text", "text": text})
    if not blocks:
        return ""
    if len(blocks) == 1:
        return blocks[0]["text"]
    return blocks


def _tool_result_block(message: dict[str, object]) -> native_transport.ProviderMessage:
    """``{"role": "tool", ...}`` -> 一个 ``tool_result`` 块。"""
    call_id = (
        _first_str(message, "tool_call_id")
        or _first_str(message, "tool_use_id")
        or _first_str(message, "id")
        or _first_str(message, "name")
        or ""
    )
    block: native_transport.ProviderMessage = {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": _tool_result_content(message.get("content")),
    }
    if message.get("is_error") is True or message.get("error") is True:
        block["is_error"] = True
    return block


def _user_blocks(message: dict[str, object]) -> list[native_transport.ProviderMessage]:
    """普通 user 消息的块列表。"""
    blocks: list[native_transport.ProviderMessage] = []
    for block in _raw_blocks(message.get("content")):
        converted = _native_content_block(block)
        if converted is not None:
            blocks.append(converted)
    return blocks


def _assistant_blocks(message: dict[str, object]) -> list[native_transport.ProviderMessage]:
    """assistant 消息的块列表：thinking 在前，文本居中，``tool_use`` 收尾。

    Anthropic 要求 ``thinking`` 块必须排在其它内容之前，否则会被当成无效组合。
    """
    native: list[native_transport.ProviderMessage] = []
    tool_uses: list[native_transport.roviderMessage] = []
    for block in _raw_blocks(message.get("content")):
        converted = _native_content_block(block)
        if converted is None:
            continue
        if str(converted.get("type")) == "tool_use":
            tool_uses.append(converted)
        else:
            native.append(converted)

    # content 里自带的 tool_use 与 tool_calls 可能重复（同一个 id 只留一个）。
    seen_ids = {str(entry.get("id")) for entry in tool_uses if entry.get("id") is not None}
    for entry in _tool_use_blocks(message):
        if str(entry.get("id")) in seen_ids:
            continue
        seen_ids.add(str(entry.get("id")))
        tool_uses.append(entry)

    head = [entry for entry in native if str(entry.get("type")) in _THINKING_BLOCK_TYPES]
    rest = [entry for entry in native if str(entry.get("type")) not in _THINKING_BLOCK_TYPES]
    return head + rest + tool_uses

def split_messages(
    messages: list[chat_model.JsonObject] | None,
) -> tuple[str | None, list[native_transport.ProviderMessage]]:
    """把 runtime 侧消息拆成 ``(系统提示, Anthropic 消息列表)``。

    系统提示用 :data:`DEFAULT_SYSTEM_SEPARATOR` 拼接后单独返回（Anthropic 的
    ``system`` 是独立字段，混在 ``messages`` 里会被拒）；没有系统消息时返回
    ``None``——调用方据此决定要不要带 ``system`` 字段。

    剩下的消息会被整形：角色归一、同角色合并、``tool_use`` / ``tool_result``
    配对补齐、首轮保证是 ``user``。
    """
    system_parts: list[str] = []
    api_messages: list[native_transport.ProviderMessage] = []

    for message in _as_sequence(messages):
        role = message.get("role","user")
        if  role == "system":
            system_parts.extend(_system_text_parts(message.get("content","")))
            continue
        api_messages.append(_project_message(message,role))
    if api_messages and api_messages[0].get("role")!="user":
        api_messages.insert(0,{"role":"user", "content":"Continue."})
    system_text ="\n\n".join(system_parts) if system_parts else None
    return system_text, _merge_consecutive_messages(api_messages)

def _system_text_parts(content:object)-> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return parts

def _project_message(message: chat_model.JsonObject, role:object)->native_transport.ProviderMessage:
    if role == "tool":
        return _tool_result_message(message)
    if role == "assistant":
        return _assistant_message(message)
    content = message.get("content","")
    if isinstance(content, (str,list)):
        return {"role":"user", "content": content}
    return  {"role":"user", "content": str(content)}

def _tool_result_message(message: chat_model.JsonObject)-> native_transport.ProviderMessage:
    tool_call_id = message.get("tool_call_id","")
    if not isinstance(tool_call_id,str):
        tool_call_id=""
    content = message.get("content","")
    text_blocks = [{"type":"text", "text":str(content)}] if content else []
    return {
        "role":"user",
        "content":[
            {
                "type":"tool_result",
                "tool_use_id":tool_call_id,
                "content":text_blocks,
            }
        ],
    }

def _assistant_message(message: chat_model.JsonObject)-> native_transport.ProviderMessage:
    content_blocks = _assistant_content_blocks(message.get("content"))
    content_blocks.extend(_tool_use_blocks(message.get("tool_calls")))
    if not content_blocks:
        content_blocks.append({"type":"text", "text":""})
    return {"role":"assistant", "content":content_blocks}

def _assistant_content_blocks(content:object)->list[native_transport.ProviderMessage]:
    if isinstance(content, str):
        return [{"type":"text", "text":content}] if content else []
    if not isinstance(content, list):
        return []
    blocks : list[native_transport.ProviderMessage] = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        elif isinstance(block, str) and block:
            blocks.append({"type":"text", "text":block})
    return blocks


def _normalized_markers(dynamic_markers: typing.Iterable[str] | None) -> tuple[str, ...]:
    """把动态段标记归一成去空白的字符串元组（单个字符串也算一个标记）。"""
    if dynamic_markers is None:
        return ()
    if isinstance(dynamic_markers, str):
        candidates: list[object] = [dynamic_markers]
    else:
        try:
            candidates = list(dynamic_markers)
        except TypeError:
            return ()
    return tuple(
        str(marker).strip() for marker in candidates if marker is not None and str(marker).strip()
    )







