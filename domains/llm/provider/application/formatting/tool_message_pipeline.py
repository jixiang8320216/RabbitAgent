import json
import logging

import pydantic
import typing
import collections

import domains.agent.domain.value_objects.message as message_module

type JsonObject=message_module.JsonObject
logger = logging.getLogger(__name__)
_JSON_OBJECT_ADAPTER= pydantic.TypeAdapter(JsonObject)
type FormattedMessages=list[pydantic.JsonValue]
type ArgumentMarkerFixer= collections.abc.Callable[[str], str]
type ArgumentCompressor=collections.abc.Callable[[JsonObject,str,int],JsonObject]
type ToolNameInferrer= collections.abc.Callable[[str],str]
class ToolMessageIndex(pydantic.BaseModel):
    assistant_call_ids: set[str]=pydantic.Field(default_factory=set)
    assistant_call_names: dict[str,str]=pydantic.Field(default_factory=dict)
    result_ids:set[str]=pydantic.Field(default_factory=set)

_PRESERVED_ARGUMENT_KEYS=(
    "file_path",
    "description",
    "expected_outcome",
    "name",
    "query",
    "content",
)


def as_formatted_message(messages: list[object])->FormattedMessages:
    return typing.cast(FormattedMessages, messages)

def fix_glm_markers_in_messages(
    messages: FormattedMessages,
    *,
    fix_markers: ArgumentMarkerFixer,
) -> int:
    fixed_count = 0
    for message in _message_objects(messages):
        if message.get("role")!= "assistant":
            continue
        for function in _message_tool_functions(message):
            original_arguments = function.get("arguments")
            if not isinstance(original_arguments, str):
                continue
            fixed_arguments = fix_markers(original_arguments)
            if fixed_arguments == original_arguments:
                continue
            function["arguments"] = fixed_arguments
            fixed_count += 1
    if fixed_count:
        logger.warning("fixed %s tool calls with glm markers", fixed_count)
    return fixed_count


def repair_formatted_tool_messages(
    messages: FormattedMessages,
    *,
    infer_tool_name: ToolNameInferrer,
) -> int:
    index= _build_tool_message_index(messages)
    repaired_count = _complete_isolated_results(messages, index)
    repaired_count += _complete_missing_results(messages, index)
    repaired_count+=_repair_existing_fields(messages, index,infer_tool_name)
    return repaired_count


def _compress_tool_arguments(
    messages: FormattedMessages,
    *,
    max_bytes: int ,
    fix_markers: ArgumentMarkerFixer,
    _compress_arguments: ArgumentCompressor,
) -> None:
    """把超长的 tool_call参数压成占位摘要，原地写回。

    先用 ``fix_markers`` 洗掉 GLM 标记再解析——带标记的串根本不是合法
    JSON，不洗则无从下手。只有 ``len(json.dumps(args)) > max_bytes`` 时才
    动手，压完再写回，让下一轮少传很多 token。

    ``max_bytes <= 0`` 表示不限制（对应 ``tool_args_compression_limit``
    的默认值 ``-1``），此时直接返回、不做任何解析。

    压缩策略由 ``_compress_arguments`` 注入；两者缺一时只做标记清洗，
    不压缩——这样即便调用方还没接上压缩器，也不会把参数洗坏。
    """
    if max_bytes  <= 0:
        return

    for function in _tool_functions(messages):
        arguments= function.get("arguments")
        if not isinstance(arguments, str) or len(arguments.encode("utf-8")) <= max_bytes:
            continue
        tool_name=_text(function.get("name"), "unknown")
        original_length = len(arguments.encode("utf-8"))
        function["arguments"] = _compress_arguments_text(
            arguments,
            tool_name= tool_name,
            original_length= original_length,
            max_bytes= max_bytes,
            fix_markers= fix_markers,
            compress_arguments= _compress_arguments,
            )

def  _tool_functions(messages: FormattedMessages)-> collections.abc.Iterable[JsonObject]:
     for message in _message_objects(messages):
        yield from _message_tool_functions(message)

def _message_tool_functions(message:JsonObject)-> collections.abc.Iterable[JsonObject]:
    for tool_call in _tool_call_objects(message):
        function = tool_call.get("function")
        if isinstance(function, dict):
             yield typing.cast(JsonObject, function)

def _tool_call_objects(message:JsonObject)-> collections.abc.Iterable[JsonObject]:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            yield typing.cast(JsonObject, tool_call)

def _message_objects(messages: FormattedMessages)-> collections.abc.Iterable[JsonObject]:
    for message in messages:
        if isinstance(message, dict):
            yield typing.cast(JsonObject, message)

def _text(value: pydantic.JsonValue|None, default: str)->str:
    return value if isinstance(value, str) and value else default


# 缺失工具结果时补的占位内容。必须是**非空**字符串：OpenAI 侧对
# ``role:"tool"`` 的 ``content`` 为空会直接 400，补一条空串等于没补。
_MISSING_RESULT_PLACEHOLDER = "[tool result not recorded]"

# 孤立结果反查不到工具名时用的兜底名。
_UNKNOWN_TOOL_NAME = "unknown_tool"


def _index_assistant_calls(message: JsonObject, index: ToolMessageIndex) -> None:
    """把一条 assistant 消息里的 tool_calls 登记进索引。

    只采集、不判断好坏：缺 id、缺名字或 function 不是 dict 的坏数据
    一律跳过，怎么补救交给后面的三个修补 pass 决定。
    """
    for tool_call in _tool_call_objects(message):
        call_id = _text(tool_call.get("id"), "")
        if not call_id:
            continue
        index.assistant_call_ids.add(call_id)
        function = tool_call.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                index.assistant_call_names[call_id] = name


def _build_tool_message_index(messages: FormattedMessages) -> ToolMessageIndex:
    index = ToolMessageIndex()
    for message in _message_objects(messages):
        role = message.get("role")
        if role == "assistant":
            _index_assistant_calls(message,index)
        elif role == "tool":
            result_id = message.get("tool_call_id")
            if isinstance(result_id,str) and result_id:
                index.result_ids.add(result_id)
    return index


def _complete_isolated_results(
    messages: FormattedMessages,
    index: ToolMessageIndex,
) -> int:
    repaired_count = 0
    insertions: list[tuple[int, JsonObject]] = []
    for position, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "tool":
            continue
        call_id = _text(message.get("tool_call_id"), "")
        # 没有 id 的结果不归这里管——那是字段缺失，由 _repair_existing_fields 修。
        if not call_id or call_id in index.assistant_call_ids:
            continue
        insertions.append((position, _synthetic_assistant_call(call_id, message)))
        index.assistant_call_ids.add(call_id)
        repaired_count += 1
    for position, synthesized in reversed(insertions):
        messages.insert(position, synthesized)
    if repaired_count:
        logger.warning(
            "completed %s isolated tool results with synthetic assistant calls",
            repaired_count,
        )
    return repaired_count


def _complete_missing_results(
    messages: FormattedMessages,
    index: ToolMessageIndex,
) -> int:
    """给「有 assistant tool_call 却没有结果」的调用补上占位结果。

    成因多是模型吐了 tool_call 但执行侧没回填（被中断 / 工具超时 / 异常吞掉）。
    不补的话 OpenAI 会以 "tool_call_id did not have response messages" 拒收。

    补法是在该 assistant 消息**之后**按原 ``tool_calls`` 顺序插入若干条
    ``role:"tool"`` 占位结果。同步把新 id 写回 ``index.result_ids``，防止同一轮
    里重复补。

    返回补上的条数。
    """
    completed = 0
    insertions: list[tuple[int, JsonObject]] = []
    for position, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        pending = [
            tool_call
            for tool_call in _tool_call_objects(message)
            if _text(tool_call.get("id"), "")
            and _text(tool_call.get("id"), "") not in index.result_ids
        ]
        if not pending:
            continue
        offset = position + 1
        for tool_call in pending:
            call_id = _text(tool_call.get("id"), "")
            insertions.append((offset, _synthetic_tool_result(call_id, tool_call)))
            index.result_ids.add(call_id)
            offset += 1
            completed += 1
    for position, synthesized in reversed(insertions):
        messages.insert(position, synthesized)
    if completed:
        logger.warning("completed %s missing tool results with placeholders", completed)
    return completed


def _repair_existing_fields(
    messages: FormattedMessages,
    index: ToolMessageIndex,
    infer_tool_name: ToolNameInferrer,
) -> int:
    """修已有消息上的字段缺失，返回修动的字段数。

    修三类，都只补缺失、不覆盖已有值：

    1. assistant ``tool_calls[].function.name`` 为空 -> 用 ``infer_tool_name``
       按 ``arguments`` 反查（GLM 的典型症状：只吐参数不吐工具名）；
    2. assistant ``tool_calls[].function.arguments`` 不是字符串 -> 补 ``"{}"``
       （OpenAI 要求它是 JSON **字符串**，给 dict 会 400）；
    3. ``role:"tool"`` 消息缺 ``tool_call_id`` -> 按 ``name`` 去
       ``index.assistant_call_names`` 反查一个还没被占用的 id 补上。

    ``infer_tool_name`` 抛异常时只记日志、该条跳过——反查是尽力而为，
    不该让一整轮格式化失败。
    """
    repaired = 0
    # name -> 还没被结果占用的 call id，供第 3 类反查。
    unused_by_name: dict[str, list[str]] = {}
    for call_id, name in index.assistant_call_names.items():
        if call_id not in index.result_ids:
            unused_by_name.setdefault(name, []).append(call_id)

    for message in _message_objects(messages):
        role = message.get("role")
        if role == "assistant":
            for function in _message_tool_functions(message):
                name = function.get("name")
                if not (isinstance(name, str) and name):
                    inferred = _infer_tool_name_safely(
                        infer_tool_name, function.get("arguments")
                    )
                    if inferred:
                        function["name"] = inferred
                        repaired += 1
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    function["arguments"] = "{}"
                    repaired += 1
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                continue
            name = _text(message.get("name"), "")
            candidates = unused_by_name.get(name, []) if name else []
            if not candidates:
                continue
            recovered = candidates.pop(0)
            message["tool_call_id"] = recovered
            index.result_ids.add(recovered)
            repaired += 1
            logger.warning(
                "recovered missing tool_call_id %r for tool result %r", recovered, name
            )
    return repaired


def _compress_arguments_text(
    arguments: str,
    *,
    tool_name: str,
    original_length: int,
    max_bytes: int,
    fix_markers: ArgumentMarkerFixer,
    compress_arguments: ArgumentCompressor,
) -> str:
    """把一段超长的 tool_call 参数串压成等长或更短的 JSON 字符串。

    调用方（:func:`_compress_tool_arguments`）已经确认 ``arguments`` 是字符串
    且超过 ``max_bytes``，所以这里**必须**返回字符串——它是被原地写回
    ``function["arguments"]`` 的。

    步骤：洗 GLM 标记 -> 解析 -> 交给注入的 ``compress_arguments`` 压结构 ->
    重新序列化。任何一步失败都退化成 :func:`_compressed_placeholder`（一段
    含工具名与原始字节数的短 JSON），**绝不返回原串**——那会让压缩白做，
    也绝不抛异常——压缩是省 token 的优化，不该把一轮对话弄挂。

    ``max_bytes <= 0`` 表示不限制，原样返回（与 :func:`_compress_tool_arguments`
    的门槛保持一致）。
    """
    try:
        arguments_object =_JSON_OBJECT_ADAPTER.validate_json(fix_markers(arguments))
    except pydantic.ValidationError:
        return "{}"
    compressed = compress_arguments(arguments_object, tool_name, max_bytes)
    compressed_text =json.dumps(compressed, ensure_ascii=False)
    if len(compressed_text.encode("utf-8")) <= max_bytes:
        return compressed_text
    minimal_text = json.dumps(_minimal_arguments(arguments_object), ensure_ascii=False)
    return minimal_text

def _minimal_arguments(arguments:JsonObject)->JsonObject:
    minimal: JsonObject = {}
    for key in _PRESERVED_ARGUMENT_KEYS:
        if key not in arguments:
            continue
        minimal[key] = "[COMPRESSED]" if key=="content" else arguments[key]
    return minimal


def _compressed_placeholder(tool_name: str, original_length: int) -> str:
    """生成"参数已压缩"占位串：短、合法 JSON、带得出上下文信息。"""
    return json.dumps(
        {
            "_compressed": True,
            "_tool": tool_name,
            "_original_bytes": original_length,
            "_note": "arguments omitted to fit the context window",
        },
        ensure_ascii=False,
    )


def _synthetic_assistant_call(call_id: str, result_message: JsonObject) -> JsonObject:
    """为一条孤立工具结果造一条合成的 assistant tool_call 消息。"""
    name = _text(result_message.get("name"), "") or _UNKNOWN_TOOL_NAME
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _synthetic_tool_result(call_id: str, tool_call: JsonObject) -> JsonObject:
    """为一条没有结果的 tool_call 造一条合成的 ``role:"tool"`` 占位结果。"""
    function = tool_call.get("function")
    name = (_text(function.get("name"), "") if isinstance(function, dict) else "") or (
        _UNKNOWN_TOOL_NAME
    )
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": _MISSING_RESULT_PLACEHOLDER,
        "name": name,
    }


def _infer_tool_name_safely(
    infer_tool_name: ToolNameInferrer,
    arguments: pydantic.JsonValue | None,
) -> str:
    """调 ``infer_tool_name`` 反查工具名；任何失败都返回空串。

    反查器是调用方注入的（``DynamicToolListFormatter._infer_tool_name_from_args``），
    它内部会 ``json.loads`` 并可能抛错。这里统一兜住：反查不到只是少一个名字，
    不该让整轮格式化失败。
    """
    if not isinstance(arguments, str) or not arguments:
        return ""
    try:
        inferred = infer_tool_name(arguments)
    except Exception as exc:  # noqa: BLE001 - 反查是尽力而为的旁路能力
        logger.debug("inferring tool name from arguments failed: %s", exc)
        return ""
    return inferred if isinstance(inferred, str) else ""

