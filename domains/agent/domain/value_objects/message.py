import collections.abc
import datetime as datetime_module
import typing

import shortuuid
import pydantic
import pydantic_core


type JsonObject =dict[str,pydantic.JsonValue]
type MessageRole = typing.Literal["user","assistant","system"]
_JSON_OBJECT_ADAPTER=pydantic.TypeAdapter(JsonObject)
_JSON_LIST_ADAPTER= pydantic.TypeAdapter(list[pydantic.JsonValue])

def _require_literal(value:object,*,field:str,expected:str)->str:
    actual= _require_string(value, field=field)
    if actual != expected:
        raise ValueError(f"{actual} != {expected}")
    return actual


type ContentBlock=(
    ToolUseBlock
    | ToolResultBlock
    | TextBlock
    | ThinkingBlock
    | RedactedThinkingBlock
    | ImageBlock
    | AudioBlock
    | VideoBlock
)
type StreamContentBlock= ContentBlock | ToolUseDeltaBlock

type ContentBlockTypes= typing.Literal[
    "text",
    "thinking",
    "redacted_thinking",
    "tool_use",
    "tool_result",
    "image",
    "audio",
    "video",
]

def _normalize_media_source(value:MediaSource|collections.abc.Mapping[str, object])->MediaSource:
    if isinstance(value,(Base64Source,URLSource)):
        return value
    return _media_source_from_mapping(value)

class _ContentBlockBase(dict[str,pydantic.JsonValue]):
    """内容块基类。

    内容块本质上是一个 ``dict``，可以直接被 ``json.dumps`` 序列化，
    便于原样回传给模型或落库。子类通过 ``_fields`` 声明允许出现的键、
    通过 ``_required`` 声明必填键；构造时会拒绝未知键并校验必填键。
    """
    __slots__=()

    _fields: typing.ClassVar[frozenset[str]] = frozenset()
    _required: typing.ClassVar[frozenset[str]] = frozenset()

    def __init__(self, **kwargs: object) -> None:
        """校验字段后写入 dict：拒绝未知键，校验必填键。"""
        unknown = set(kwargs) - self._fields
        if unknown:
            names = ",".join(sorted(unknown))
            raise TypeError(f" {names} is not allowed in {type(self).__name__}")
        missing = self._required - set(kwargs)
        if missing:
            names = ",".join(sorted(missing))
            raise TypeError(f" {names} is required by {type(self).__name__}")
        super().__init__(**kwargs)

    def __getattr__(self, name: str) -> object:
        """允许以属性方式读取块内字段，缺失时抛 ``AttributeError``。"""
        try:
            return self[name]
        except KeyError:
            raise AttributeError(
                f" {type(self).__name__} has no attribute {name!r}"
            ) from None

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: type,
        handler: object,
    ) -> pydantic_core.core_schema.CoreSchema:
        """告诉 pydantic 怎么校验内容块（块本质是 ``dict`` 子类）。

        pydantic 2.13 只认**精确**的 ``dict``：拿到 dict 子类会直接抛
        ``PydanticSchemaGenerationError``。而 ``ToolResponse.content`` /
        ``ChatResponse.content`` / ``ToolCall.block`` 等字段声明成了
        ``list[ContentBlock]`` / ``ToolUseBlock``，于是这些模块在**导入期**就炸——
        进程连 ``main()`` 都进不去：``main.py`` 的 ``try/except`` 在 import 之后
        才生效，日志系统也没起来，表现就是"一闪而过、没有任何日志"。

        这里显式给 schema：先按 dict 校验，再用子类的关键字构造把**子类身份**
        还原回去——下游到处是 ``isinstance(block, message.ToolUseBlock)``，
        退化成普通 dict 会让那些分支全部失配。
        """

        def _rebuild(value: dict[str, pydantic.JsonValue]) -> "_ContentBlockBase":
            try:
                return cls(**value)
            except (TypeError, ValueError) as error:
                # 必须抛成**校验错误**而不是 TypeError：``ContentBlock`` 是个
                # union，pydantic 只认 ValidationError 才会继续试下一个分支，
                # TypeError 会直接冒泡把整轮打挂（TextBlock 的数据被拿去试
                # ToolUseBlock 是常事）。
                raise pydantic_core.PydanticCustomError(
                    "content_block_shape",
                    "{cls_name} cannot hold these fields: {error}",
                    {"cls_name": cls.__name__, "error": str(error)},
                ) from error

        return pydantic_core.core_schema.no_info_after_validator_function(
            _rebuild,
            pydantic_core.core_schema.dict_schema(),
        )

    def to_dict(self) -> JsonObject:
        """返回可 JSON 序列化的普通字典副本。"""
        return dict(self)

    def _string(self,field:str)->str:
        return _require_string(self.get(field),field=field)


class Base64Source(_ContentBlockBase):
    """Base64 内联媒体源。"""

    _fields = frozenset({"type", "media_type", "data"})
    _required = frozenset({"type", "media_type", "data"})

    def __init__(self, *, type: typing.Literal["base64"], media_type: str, data: str) -> None:
        super().__init__(type=_require_literal(type,field="type",expected="base64"),
                         media_type=_require_string(media_type,field="media_type"),
                         data=_require_string(data,field="data"),
                         )


class URLSource(_ContentBlockBase):
    """远程 URL 媒体源。"""

    _fields = frozenset({"type", "url"})
    _required = frozenset({"type", "url"})

    def __init__(self, *, type: typing.Literal["url"], url: str) -> None:
        super().__init__(type=_require_literal(type,field="type",expected="url"), url=_require_string(url,field="url"),
                         )


type MediaSource = Base64Source | URLSource

class TextBlock(_ContentBlockBase):
    """文本内容块。"""

    _fields = frozenset({"type", "text"})
    _required = frozenset({"type", "text"})

    def __init__(self, *, type: typing.Literal["text"], text: str) -> None:
        super().__init__(type=_require_literal(type,field="type",expected="text"),
                         text=_require_string(text,field="text"),
                         )



class ThinkingBlock(_ContentBlockBase):
    """模型的思考过程块。"""

    _fields = frozenset({"type", "thinking"})
    _required = frozenset({"type", "thinking"})

    def __init__(self, *, type: typing.Literal["thinking"], thinking: str) -> None:
        super().__init__(type=_require_literal(type,field="type",expected="thinking"),
                         thinking=_require_string(thinking,field="thinking"),
                         )


class RedactedThinkingBlock(_ContentBlockBase):
    """被脱敏的思考块，只保留密文。"""

    _fields = frozenset({"type", "data"})
    _required = frozenset({"type", "data"})

    def __init__(self, *, type: typing.Literal["redacted_thinking"], data: str) -> None:
        super().__init__(type=_require_literal(type, field="type", expected="redacted_thinking"),
                         data=_require_string(data, field="data"),
                         )


class ToolUseBlock(_ContentBlockBase):
    """模型发起的工具调用块。"""

    _fields = frozenset({"type", "id", "name", "input", "index"})
    _required = frozenset({"type", "id", "name", "input"})

    def __init__(
            self,
            *,
            type: typing.Literal["tool_use"],
            id: str,
            name: str,
            input: JsonObject,
            index: int | None = None,
    ) -> None:
        normalized_input = _JSON_OBJECT_ADAPTER.validate_python(input)
        payload: JsonObject = {
            "type": _require_literal(type, field="type", expected="tool_use"),
            "id":_require_string(id, field="id"),
            "name": _require_string(name, field="name"),
            "input": normalized_input,
        }
        normalized_index = _require_optional_index(index)
        if normalized_index is not None:
            payload["index"] = normalized_index
        super().__init__(**payload)


type ToolResultOutput = str | list[ContentBlock]

def is_content_block(value:object)->typing.TypeGuard[ContentBlock]:
    return isinstance(value,
                      (
                          ToolUseBlock,
                          ToolResultBlock,
                          TextBlock,
                          ThinkingBlock,
                          RedactedThinkingBlock,
                          ImageBlock,
                          AudioBlock,
                          VideoBlock,
                      ))

class ImageBlock(_ContentBlockBase):
    """图片内容块。"""

    _fields = frozenset({"type", "source"})
    _required = frozenset({"type", "source"})

    def __init__(
            self,
            *,
            type: typing.Literal["image"],
            source: MediaSource |collections.abc.Mapping[str,object],
    ) -> None:
        normalized_source = _normalize_media_source(source)
        super().__init__(type=_require_literal(type, field="type", expected="image"),
                         source=normalized_source)



class AudioBlock(_ContentBlockBase):
    """音频内容块。"""

    _fields = frozenset({"type", "source"})
    _required = frozenset({"type", "source"})

    def __init__(
            self,
            *,
            type: typing.Literal["audio"],
            source: MediaSource |collections.abc.Mapping[str,object],
    ) -> None:
        normalized_source = _normalize_media_source(source)
        super().__init__(type=_require_literal(type, field="type", expected="audio"),
                         source=normalized_source)

class ToolResultBlock(_ContentBlockBase):
    """工具执行结果块，``output`` 可以是纯文本或嵌套内容块列表。"""

    _fields = frozenset({"type", "id", "name", "output"})
    _required = frozenset({"type", "id", "name", "output"})

    def __init__(
            self,
            *,
            type: typing.Literal["tool_result"],
            id:str,
            output: str |collections.abc.Sequence[ContentBlock],
            name: str,
    ) -> None:
        normalized_output: pydantic.JsonValue
        if isinstance(output,str):
            normalized_output = output
        else:
            normalized_output = list(output)
            _JSON_LIST_ADAPTER.validate_python(normalized_output)
        super().__init__(type=_require_literal(type, field="type", expected="tool_result"),
                         id=_require_string(id, field="id"),
                         output=normalized_output,
                         name=_require_string(name, field="name"),
                        )



class VideoBlock(_ContentBlockBase):
    """视频内容块。"""

    _fields = frozenset({"type", "source"})
    _required = frozenset({"type", "source"})

    def __init__(
            self,
            *,
            type: typing.Literal["video"],
            source: MediaSource |collections.abc.Mapping[str,object],
    ) -> None:
        normalized_source = _normalize_media_source(source)
        super().__init__(type=_require_literal(type, field="type", expected="video"),
                         source=normalized_source)


def _media_source_from_mapping(data: collections.abc.Mapping[str,object])->MediaSource:
    # 先拷一份再 pop：下面一路 pop 会**改坏调用方传进来的 mapping**。同一个
    # dict 被复用（或 pydantic union 二次试探同一个块）时，"type" 已经被第一次
    # pop 走了，第二次直接 KeyError。
    data = dict(data)
    source_type = data.pop("type")
    if source_type =="base64":
        _reject_unknown_fields(
            data,
            allowed=frozenset({"type","media_type","data"}),
            contract="base64 source",
        )
        return Base64Source(
            type="base64",
            media_type=_require_string(data.pop("media_type"),field="media_type"),
            data =_require_string(data.pop("data"),field="data"),
        )
    if source_type =="url":
        _reject_unknown_fields(
            data,
            allowed=frozenset({"type","url"}),
            contract="URL source",
        )
        return URLSource(
            type="url",
            url=_require_string(data.pop("url"),field="url"),
        )
    raise ValueError(f"Unexpected source type {source_type}")

def _require_string(value:object,*, field:str) -> str:
    if not isinstance(value,str):
        raise TypeError(f" {field} must be a string")
    return value

def _require_optional_index(value:object,*, field:str="index") -> int|None:
    """校验可选的流式索引字段：必须是非负整数或 None。"""
    if value is None:
        return None
    if isinstance(value,bool) or not isinstance(value,int):
        raise TypeError(f" {field} must be an integer or None")
    if value < 0:
        raise TypeError(f" {field} must be a non-negative integer")
    return value

def is_stream_content_block(value:object)->typing.TypeGuard[StreamContentBlock]:
    return is_content_block(value) or isinstance(value,ToolUseDeltaBlock)

def stream_content_block_from_mapping(
    data:collections.abc.Mapping[str,object],
)->StreamContentBlock:
    if is_stream_content_block(data):
        return data
    block_type = data.get("type")
    if block_type !="tool_use_delta":
        try:
            return content_block_from_dict(data)
        except ValueError as e:
            raise ValueError(f" {e} ")
    _reject_unknown_fields(
        data,
        allowed=frozenset({"type","input_delta","input_delta_kind","id","name","index"}),
        contract="tool use delta block",
    )
    input_delta_kind=data.get("input_delta_kind","append")
    if input_delta_kind not in ("append","snapshot"):
        raise TypeError(f" {input_delta_kind} must be either 'append' or 'remove'")
    return ToolUseDeltaBlock(
        type="tool_use_delta",
        input_delta=_require_string(data.get("input_delta"),field="input_delta"),
        id=_require_optional_string(data.get("id"),field="id"),
        name=_require_optional_string(data.get("name"),field="name"),
        index=_require_optional_index(data.get("index")),
        input_delta_kind=typing.cast(typing.Literal["append","remove"],input_delta_kind),
    )

def _reject_unknown_fields(
    data: collections.abc.Mapping[str,object],
    *,
    allowed:frozenset[str],
    contract:str,
)->None:
    unknown=set(data)-allowed
    if unknown:
        name=",".join(sorted(unknown))
        raise TypeError(f" {name} must be a frozen set")


def content_block_from_dict(data:collections.abc.Mapping[str,object])->ContentBlock:
    if is_content_block(data):
        return data
    block_type = data.get("type")
    if not isinstance(block_type,str):
        raise TypeError(f" {block_type} must be a string")

    if block_type =="text":
        _reject_unknown_fields(data,allowed=frozenset({"type","text"}), contract="text block")
        return TextBlock(
            type="text",
            text=_require_string(data.get("text"),field="text"),
        )
    if block_type =="thinking":
        _reject_unknown_fields(data, allowed=frozenset({"type","thinking"}), contract="thinking block")
        return ThinkingBlock(
               type="thinking",
              thinking=_require_string(data.get("thinking"),field="thinking"),
        )
    if block_type =="redacted_thinking":
        _reject_unknown_fields(
        data,
            allowed=frozenset({"type","data"}),
            contract="redacted thinking block",
        )
        return RedactedThinkingBlock(
            type="redacted_thinking",
            data=_require_string(data.get("data"),field="data"),
        )
    if block_type =="tool_use":
        _reject_unknown_fields(
            data,
            allowed=frozenset({"type","id","name","input","index"}),
            contract="tool use block",
        )
        raw_input=data.get("input")
        if not isinstance(raw_input,collections.abc.Mapping):
            raise TypeError(f" {raw_input} must be a mapping")
        return ToolUseBlock(
            type="tool_use",
            id=_require_string(data.get("id"),field="id"),
            name=_require_string(data.get("name"),field="name"),
            input=_JSON_OBJECT_ADAPTER.validate_python(raw_input),
            index=_require_optional_index(data.get("index")),
        )

    if block_type =="tool_result":
        _reject_unknown_fields(
            data,
            allowed=frozenset({"type","id","name","output"}),
            contract="tool result block",
        )
        raw_output=data.get("output")
        if isinstance(raw_output,str):
            output:ToolResultOutput =raw_output
        elif isinstance(raw_output,list):
            output=[
                item if is_content_block(item) else content_block_from_dict(item)
                for item in raw_output
                if isinstance(item,collections.abc.Mapping)
            ]
            if len(output)!=len(raw_output):
                raise TypeError(f" {output} must have {len(raw_output)} items")
        else:
            raise TypeError(f" {raw_output} must be a string")
        return ToolResultBlock(
            type="tool_result",
            id=_require_string(data.get("id"),field="id"),
            name=_require_string(data.get("name"),field="name"),
            output=output,
        )
    if block_type in {"image","audio","video"}:
        _reject_unknown_fields(data,allowed=frozenset({"type","source"}), contract=f"{block_type} block")
        source_value=data.get("source")
        if not isinstance(source_value,collections.abc.Mapping):
            raise TypeError(f" {source_value} must be a mapping")
        source= _media_source_from_mapping(source_value)
        if block_type == "image":
            return ImageBlock(
                type="image",
                source=source,
            )
        if block_type == "audio":
            return AudioBlock(type="audio", source=source)
        return VideoBlock(type="video", source=source)
    raise TypeError(f" {block_type} block type not supported")


def _jsonify(value: object) -> pydantic.JsonValue:
    """把值递归转成只由 JSON 原生类型构成的**普通**容器。

    ``ContentBlock`` / ``MediaSource`` 都是 ``dict`` 的子类，直接落进快照也能被
    ``json.dumps`` 序列化，但：

    - 严格模式（``strict=True``）的 pydantic 模型不认 dict 子类；
    - 反序列化回来的对象只有普通 ``dict``，两者比较会不相等。

    所以统一转成普通 ``dict`` / ``list``，保证「运行时对象」与「还原后的 JSON」
    得到完全一致的 Python 值。无法表达的类型降级成字符串，不抛异常。
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, collections.abc.Mapping):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if isinstance(value, (collections.abc.Sequence, collections.abc.Set)):
        return [_jsonify(item) for item in value]
    return str(value)


def _require_message_role(value: object) -> MessageRole:
    """校验消息角色：必须与 :meth:`Msg.__init__` 接受的三个取值一致。"""
    role = _require_string(value, field="role")
    if role not in ("user", "assistant", "system"):
        raise TypeError(f"Unexpected role {role}")
    return typing.cast(MessageRole, role)


def _content_from_json(value: object) -> str | list[ContentBlock]:
    """把反序列化后的 ``content`` 还原成运行时形态（纯文本或内容块列表）。

    先走 :func:`_jsonify` 深拷贝，避免 :func:`content_block_from_dict` 内部的
    ``pop`` 破坏调用方传入的字典。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, collections.abc.Mapping):
        return [content_block_from_dict(_plain_block(value))]
    if isinstance(value, collections.abc.Sequence):
        blocks: list[ContentBlock] = []
        for item in value:
            if isinstance(item, str):
                blocks.append(TextBlock(type="text", text=item))
            elif isinstance(item, collections.abc.Mapping):
                blocks.append(content_block_from_dict(_plain_block(item)))
            else:
                raise TypeError(f"Unexpected content block type {type(item)!r}")
        return blocks
    raise TypeError(f"Unexpected content type {type(value)!r}")

def _require_optional_string(value:object, *, field:str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field=field)

def _plain_block(value: collections.abc.Mapping[str, object]) -> collections.abc.Mapping[str, object]:
    """深拷贝一个内容块，并保证它是普通 ``dict``。"""
    plain = _jsonify(value)
    if not isinstance(plain, collections.abc.Mapping):
        raise TypeError(f"Unexpected content block type {type(value)!r}")
    return plain


def _collect_text(
        value: object,
        *,
        parts: list[str],
        include_thinking: bool,
        include_tool_result: bool,
) -> None:
    """递归收集纯文本：只取文本块，思考/工具结果按开关决定是否纳入。"""
    if isinstance(value, str):
        if value:
            parts.append(value)
        return
    if isinstance(value, collections.abc.Mapping):
        block_type = value.get("type")
        if block_type == "text":
            _collect_text(
                value.get("text"),
                parts=parts,
                include_thinking=include_thinking,
                include_tool_result=include_tool_result,
            )
        elif block_type == "thinking" and include_thinking:
            _collect_text(
                value.get("thinking"),
                parts=parts,
                include_thinking=include_thinking,
                include_tool_result=include_tool_result,
            )
        elif block_type == "tool_result" and include_tool_result:
            _collect_text(
                value.get("output"),
                parts=parts,
                include_thinking=include_thinking,
                include_tool_result=include_tool_result,
            )
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_text(
                item,
                parts=parts,
                include_thinking=include_thinking,
                include_tool_result=include_tool_result,
            )

class ToolUseDeltaBlock(_ContentBlockBase):
    def __init__(
            self,
            *,
            type: typing.Literal["tool_use_delta"],
            input_delta: str,
            id: str | None = None,
            name: str | None = None,
            index: int | str | None = None,
            input_delta_kind: typing.Literal["append", "snapshot"] = "append",
    ) -> None:
        if input_delta_kind not in ("append", "snapshot"):
            raise ValueError(
                " input_delta_kind must be one of 'append','snapshot'"
            )
        payload: JsonObject = {
            "type": _require_literal(type, field="type", expected="tool_use_delta"),
            "input_delta": _require_string(input_delta, field="input_delta"),
        }
        if input_delta_kind == "snapshot":
            payload["input_delta_kind"] = input_delta_kind
        normalized_id = _require_optional_string(id, field="id")
        if normalized_id is not None:
            payload["id"] = normalized_id
        normalized_name = _require_optional_string(name, field="name")
        if normalized_name is not None:
            payload["name"] = normalized_name
        normalized_index = _require_optional_index(index)
        if normalized_index is not None:
            payload["index"] = normalized_index
        super().__init__(**payload)


def _normalize_delta_index(value: object) -> int | None:
    """把增量块的 ``index`` 归一成 ``int | None``。

    除了 :class:`ToolUseBlock` 那种 ``int``，这里也收**数字字符串**：上游
    ``stream_content_block_mapping`` 是用 ``_require_optional_string`` 取的
    ``index``，从 JSON 过来的索引常常是 ``"0"`` 这种形态；而下游
    ``PendingToolCall.index`` 是 strict pydantic 的 ``int | None``、
    ``_tool_id_by_index`` 也是 ``dict[int, str]``，字符串进不去。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(" index must be a non-negative integer or None")
    if isinstance(value, int):
        return _require_optional_index(value)
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise TypeError(" index must be a non-negative integer or None")
        return _require_optional_index(int(text))
    raise TypeError(" index must be a non-negative integer or None")


class Msg:
    __slots__ = ("name", "content", "role", "metadata", "id", "timestamp", "invocation_id")

    name: str
    content: str | list[ContentBlock]
    role: MessageRole
    metadata: JsonObject | None
    id: str
    timestamp: str
    invocation_id: str | None

    def __init__(
            self,
            name: str,
            content: str | collections.abc.Sequence[ContentBlock | collections.abc.Mapping[str, object]],
            role: MessageRole,
            metadata: JsonObject | None = None,
            timestamp: str|None = None,
            invocation_id: str | None = None,
    )->None:
        self.name = _require_string(name,field="name")
        if isinstance(content, str):
            self.content = content
        elif isinstance(content, collections.abc.Sequence):
            blocks: list[ContentBlock]=[]
            for block in content:
                if is_content_block(block):
                    blocks.append(block)
                elif isinstance(block, collections.abc.Mapping):
                    blocks.append(content_block_from_dict(block))
                else:
                    raise TypeError(f"Unexpected content type {type(block)}")
            self.content = blocks
        else:
            raise TypeError(f"Unexpected content type {type(content)}")
        if role not in ("user","assistant","system"):
            raise TypeError(f"Unexpected role {role}")
        self.role = role
        self.metadata = None if metadata is None else _JSON_OBJECT_ADAPTER.validate_python(metadata)
        self.id = shortuuid.uuid()
        self.timestamp = timestamp or datetime_module.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        self.invocation_id = invocation_id

    # ------------------------------------------------------------------
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> JsonObject:
        """导出成快照字典。

        键集合与 :class:`runtime.model.conversation_context.ContextMessageState`
        一一对应（该模型是 ``extra="forbid"``，多一个键都会校验失败），顺序为
        ``id`` / ``name`` / ``role`` / ``content`` / ``metadata`` / ``timestamp`` /
        ``invocation_id``。内容块会被转成普通 ``dict``，保证严格模式校验与相等性
        判断都成立。
        """
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "content": self.content,
            "metadata": self.metadata ,
            "timestamp": self.timestamp,
            "invocation_id": self.invocation_id,
        }

    @classmethod
    def from_dict(cls, json_data: collections.abc.Mapping[str, object]) -> Msg:
        """从快照字典还原消息，是 :meth:`to_dict` 的逆操作。

        ``__init__`` 总会重新生成 ``id``，所以这里在构造后显式回填字典里的
        ``id``（有则还原，没有则保留新生成的），否则消息身份会静默改变。
        传入 :class:`Msg` 时原样返回，方便调用方不做类型判断。
        """
        _reject_unknown_fields(json_data,allowed=frozenset({"id","name","role","content","metadata","timestamp","invocation_id"}),
                             contract="message",  )
        raw_content = json_data.get("content")
        if isinstance(raw_content, str):
            content:str|list[ContentBlock] = raw_content
        elif isinstance(raw_content, list):
            content=[]
            for item in raw_content:
                if not isinstance(item, collections.abc.Mapping):
                    raise TypeError(f"Unexpected content type {type(item)}")
                content.append(content_block_from_dict(item))
        else:
            raise TypeError(f"Unexpected content type {type(raw_content)}")
        raw_role = json_data.get("role")
        if raw_role not in ("user","assistant","system"):
            raise TypeError(f"Unexpected role {raw_role}")
        role = typing.cast(MessageRole, raw_role)
        raw_metadata = json_data.get("metadata")
        if raw_metadata is not None and not isinstance(raw_metadata, collections.abc.Mapping):
            raise TypeError(f"Unexpected metadata {type(raw_metadata)}")
        metadata = None if raw_metadata is None else _JSON_OBJECT_ADAPTER.validate_python(raw_metadata)
        timestamp = _require_optional_string(json_data.get("timestamp"),field="timestamp")
        invocation_id = _require_optional_string(json_data.get("invocation_id"),field="invocation_id")

        message = cls(
            name=_require_string(json_data.get("name"), field="name"),
            content=content,
            role=role,
            metadata=metadata,
            timestamp=timestamp,
            invocation_id=invocation_id,
        )
        identifier = json_data.get("id")
        if identifier is not None:
            message.id = _require_string(identifier, field="id")
        return message

    def get_text_content(
            self,
            separator: str = "\n",
    ) -> str|None:
        """取出消息的纯文本部分，用于展示、日志与遥测。

        纯文本消息原样返回；内容块消息只取文本块（思考与工具结果默认排除，
        需要时显式开启），图片 / 音频 / 视频 / 工具调用等无文本的块跳过。
        """
        if isinstance(self.content, str):
            return self.content
        gathered_text =[block.text for block in self.content if isinstance(block, TextBlock)]
        return separator.join(gathered_text) if gathered_text else None

    def has_content_blocks(self, block_type: ContentBlockTypes|None=None) -> bool:
        blocks= self.get_content_blocks()
        if block_type is None:
            return bool(blocks)
        return any(block.get("type")==block_type for block in blocks)

    @typing.overload
    def get_content_blocks(self, block_type:typing.Literal["text"])->collections.abc.Sequence[TextBlock]:...

    @typing.overload
    def get_content_blocks(self, block_type:typing.Literal["tool_use"])->collections.abc.Sequence[ToolUseBlock]:...

    @typing.overload
    def get_content_blocks(self, block_type:typing.Literal["tool_result"])->collections.abc.Sequence[ToolResultBlock]:...

    @typing.overload
    def get_content_blocks(self, block_type:None=None)->collections.abc.Sequence[ContentBlock]:...

    def get_content_blocks(
        self,
        block_type:ContentBlockTypes|list[ContentBlockTypes]|None=None,
    )->collections.abc.Sequence[ContentBlock]:
        blocks:list[ContentBlock]
        if isinstance(self.content, str):
            blocks=[TextBlock(type="text",text=self.content)]
        else:
            blocks=list(self.content)
        if isinstance(block_type,str):
            return [block for block in blocks if block.get("type")==block_type]
        if isinstance(block_type,list):
            requested=set(block_type)
            return [block for block in blocks if block.get("type")  in requested]
        return blocks