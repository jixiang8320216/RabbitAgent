"""会话上下文的状态快照模型。

背景
----
``runtime.model.message.Msg`` 是带 ``__slots__`` 的运行时对象，content 里装的是
``ContentBlock``（``dict`` 子类）实例，并且**没有定义 ``__eq__``**——两条内容完全相同
的消息用 ``==`` 比较永远是 False。因此它既不适合直接落盘，也不适合拿来做跨层
身份核对。

本模块提供一对「纯数据 + 可校验」的快照模型，供
``app.application.orchestrators.session_turn_execution._persist_admitted_context``
做**准入身份核对**：

.. code-block:: python

    state = ContextState.model_validate(entry_agent.context.state_dict())
    expected = ContextMessageState.from_message(user_msg)
    matches = [(m, marks) for m, marks in state.content if m.id == user_msg.id]
    if not matches:
        await entry_agent.context.add(user_msg)
    elif len(matches) != 1 or matches[0] != (expected, []):
        raise RuntimeError("turn admission Context identity is ambiguous")

契约（重要）
------------
``from_message()`` 产出的字段必须与 ``context.state_dict()`` 序列化的字段**逐字对应**，
否则上面的相等性判断恒为 False，会被误判成「身份歧义」。``state_dict()`` 需要回传
这些键：``id`` / ``name`` / ``role`` / ``content`` / ``metadata`` / ``timestamp`` /
``invocation_id``。

宽容性约定
----------
``state_dict()`` 目前还没有实现，为了让本模块在它落地前后都能用，校验被刻意写得宽容：

- ``ContextState.model_validate`` 接受映射（``{"content": [...]}``）、
  裸序列（直接当 content）、以及任何带 ``state_dict()`` 方法的对象；
- content 的每一项可以是 ``(message, marks)`` 二元组/二列表、
  带 ``message``/``marks`` 键的映射，也可以直接是一条消息。

为什么没有 ``to_message()``
---------------------------
``Msg.__init__`` 总会用 ``shortuuid.uuid()`` 生成**新的** ``id``，无法还原原始消息
标识；提供反向转换只会得到一个「看起来对、实际身份已经变了」的对象，所以这里
故意不实现。需要还原时请调用方显式处理 id。
"""

from __future__ import annotations

import collections.abc
import typing
import copy
import domains.agent.domain.value_objects.message as message_module
import domains.agent.domain.value_objects.turn_identity as turn_identity

import pydantic

JsonObject=dict[str,pydantic.JsonValue]
_JSON_OBJECT_ADAPTER= pydantic.TypeAdapter(JsonObject)

class ContextPersistenceIndeterminateError(RuntimeError):
    ...

class ContextMessageState(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    id:str= pydantic.Field(min_length=1)
    name:str
    role:typing.Literal["user","assistant","system"]
    content: str|list[JsonObject]
    metadata: JsonObject |None=None
    timestamp: str=pydantic.Field(min_length=1)
    invocation_id: str |None=None

    @classmethod
    def from_message(cls, runtime_message: message_module.Msg) -> ContextMessageState:
        return cls.model_validate(runtime_message.to_dict())

    def to_message(self)->message_module.Msg:
        return message_module.Msg.from_dict(self.model_dump(mode="json"))

type ContextMark = str | dict[str, pydantic.JsonValue]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _coerce_marks(value: object) -> list[object]:
    """把 marks 归一成列表。

    ``None`` → ``[]``；字符串 → 单元素列表（不能被当成序列拆成字符）；
    其他序列 → 原样转列表；单个标记 → 包成单元素列表。
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, collections.abc.Sequence):
        return list(value)
    return [value]


def _coerce_content_item(value: object) -> tuple[object, object]:
    """把 content 的一项归一成 ``(message, marks)``。"""
    if isinstance(value, (tuple, list)) and not isinstance(value, (str, bytes)):
        if len(value) == 2:
            return value[0], _coerce_marks(value[1])
        if len(value) == 1:
            return value[0], []
        raise ValueError(
            f"context content entry must be a (message, marks) pair, got {len(value)} items"
        )
    if isinstance(value, collections.abc.Mapping):
        for key in ("message", "msg", "state"):
            if key in value:
                return value[key], _coerce_marks(value.get("marks"))
        # 整个映射就是一条消息
        return value, []
    return value, []


def _normalize_content_blocks(value: object) -> object:
    """把消息内容转成可 JSON 序列化的形态。

    ``ContentBlock`` 是 ``dict`` 的子类，直接交给 pydantic 校验也能过，但显式转成
    普通 ``dict`` 可以保证两个来源（运行时对象 / 反序列化后的 JSON）得到**完全一样**
    的 Python 值，相等性判断才可靠。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, collections.abc.Mapping):
        return [dict(value)]
    if isinstance(value, (tuple, list)):
        blocks: list[dict[str, pydantic.JsonValue]] = []
        for block in value:
            if isinstance(block, collections.abc.Mapping):
                blocks.append(dict(block))
            elif isinstance(block, str):
                blocks.append({"type": "text", "text": block})
            else:
                raise TypeError(f"Unexpected content block type {type(block)!r}")
        return blocks
    raise TypeError(f"Unexpected content type {type(value)!r}")


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------



class ContextState(pydantic.BaseModel):
    """整个上下文的状态快照。

    ``content`` 是 ``[(消息状态, [marks...]), ...]``：一条消息除了自身内容，还可以
    携带若干上下文标记（压缩边界、裁剪标记等），所以用二元组而不是裸消息列表。

    相等性：``content`` 里的每一项都是普通的 ``(ContextMessageState, list)`` 元组，
    可以直接和 ``(ContextMessageState.from_message(msg), [])`` 比较。
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    version:typing.Literal[1]=1
    content:list[tuple[ContextMessageState],list[str]]=pydantic.Field(default_factory=list)

    @pydantic.model_validator(mode="before")
    @classmethod
    def _accept_state_provider(cls, value: object) -> object:
        """允许直接传入上下文对象（带 ``state_dict()``）或裸 content 序列。"""
        if isinstance(value, ContextState):
            return value
        if isinstance(value, (str, bytes)) or isinstance(value, collections.abc.Mapping):
            return value
        dump = getattr(value, "state_dict", None)
        if callable(dump):
            return dump()
        if isinstance(value, collections.abc.Sequence):
            return {"content": value}
        return value

    @pydantic.field_validator("content", mode="before")
    @classmethod
    def _normalize_content(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, collections.abc.Sequence):
            raise ValueError("ContextState.content must be a sequence of (message, marks) pairs")
        return [_coerce_content_item(item) for item in value]

    # --- 便捷访问（调用方原本要手写循环，这里收口） -------------------------

    @property
    def messages(self) -> list[ContextMessageState]:
        """content 里的所有消息状态（丢掉 marks）。"""
        return [message for message, _marks in self.content]

    def find(self, message_id: str) -> tuple[ContextMessageState, list[ContextMark]] | None:
        """按消息 id 查找条目。

        :return: ``(消息状态, marks)``；不存在或 id 重复时返回 ``None``
            （重复属于异常状态，交由调用方决定是否报错）。
        """
        matches = [entry for entry in self.content if entry[0].id == message_id]
        if len(matches) != 1:
            return None
        return matches[0]

    def __len__(self) -> int:
        return len(self.content)

    def __iter__(self) -> typing.Iterator[tuple[ContextMessageState, list[ContextMark]]]:
        return iter(self.content)


# ---------------------------------------------------------------------------
# 运行时上下文实现
# ---------------------------------------------------------------------------
class ContextEntry(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True,extra="forbid",strict=True, validate_assignment=True )

    message:message_module.Msg
    marks:list[str]=pydantic.Field(default_factory=list)


class Context:
    """运行时会话上下文：一条按时间追加的消息序列。

    上面几个 ``*State`` 模型是**快照**，本类是**活体**。两者通过
    :meth:`state_dict` 衔接——``ContextState.model_validate(context.state_dict())``
    必须能还原出与 ``ContextMessageState.from_message(msg)`` **相等**的条目，
    否则准入身份核对会误判成「歧义」。

    因此 :meth:`state_dict` 不依赖 ``Msg.to_dict``（当前 ``Msg`` 没有该方法），
    而是自己按契约逐字段取出 ``id`` / ``name`` / ``role`` / ``content`` /
    ``metadata`` / ``timestamp`` / ``invocation_id``。

    接口按调用方的实际用法定义：

    - ``await context.add(msg)`` —— 主循环每轮追加（异步是历史契约，别改同步）；
    - ``context.append(msg)`` —— 同步别名，给不方便 await 的路径；
    - ``context.state_dict()`` —— 持久化与快照；
    - ``context.history()`` / 迭代 / ``len()`` —— 拼模型请求时取历史。
    """

    def __init__(self)->None:
        self._entries: list[ContextEntry] = []

    @property
    def context(self)->list[message_module.Msg]:
        return [entry.message for entry in self._entries]

    @property
    def current_tokens(self)->int:
        return 0

    async def get_messages(self,
        mark:str|None=None,
        exclude_mark: str|None=None,
    )->list[message_module.Msg]:
        if mark is not None and not isinstance(mark, str):
            raise ValueError("mark must be a str")
        if exclude_mark is not None and not isinstance(exclude_mark, str):
            raise ValueError("exclude_mark must be a str")
        filtered=[entry for entry in self._entries if mark is None or mark in entry.marks]
        if exclude_mark is not None:
            filtered=[entry for entry in filtered if exclude_mark not in entry.marks]
        return [entry.message for entry in filtered]

    async def add(self,
        messages: message_module.Msg|list[message_module.Msg]|None=None,
        marks: str|list[str]|None=None,
        *,
        allowed_duplicates: bool=False,
    )->None:
        if messages is None:
            return
        message_list =[messages] if isinstance(messages, message_module.Msg) else messages
        if not isinstance(message_list, list)or not all(
            isinstance(runtime_message,message_module.Msg) for runtime_message in message_list
        ):
            raise TypeError("message_list must be a list")

        if marks is None:
            normalized_marks: list[str] = []
        elif isinstance(marks, str):
            normalized_marks = [marks]
        elif isinstance(marks, list) and all(isinstance(mark, str) for mark in marks):
            normalized_marks = list(marks)
        else:
            raise TypeError("marks must be a str or list")
        seen_ids ={entry.message.id for entry in self._entries}
        for runtime_message in message_list:
            if not allowed_duplicates and runtime_message.id in seen_ids:
                continue
            message_copy= copy.deepcopy(runtime_message)
            current_turn_id = turn_identity.get_turn_id()
            if current_turn_id is not None:
                metadata = dict(runtime_message.metadata or {})
                metadata.setdefault("turn_id", current_turn_id)
                message_copy.metadata = _JSON_OBJECT_ADAPTER.validate_python(metadata)
            self._entries.append(ContextEntry(
                message=message_copy,
                marks=list(normalized_marks),))
            seen_ids.add(runtime_message.id)

    async def delete(self, msg_ids: list[str])->int:
        if not all(isinstance(msg_id, str) for msg_id in msg_ids):
            raise TypeError("msg_ids must be a list")
        inital = len(self._entries)
        ids=set(msg_ids)
        self._entries = [entry for entry in self._entries if entry.message.id not in ids]
        return inital - len(self._entries)



    async def clear(self) -> None:
        """清空上下文。"""
        self._entries.clear()

    def state_dict(self) -> dict[str, typing.Any]:
        """导出状态快照，形状与 :class:`ContextState` 一致。

        每条消息附带空的 marks 列表：标记语义由压缩/裁剪逻辑写入，本实现不做
        这些优化，保持「无标记」即可。
        """
        state =ContextState(
            content=[(ContextMessageState.from_message(entry.message), list(entry.marks)) for entry in self._entries],
        )
        return _JSON_OBJECT_ADAPTER.validate_python(state.model_dump(mode="json"))






__all__ = [
    "Context",
    "ContextMark",
    "ContextMessageState",
    "ContextState",
]
