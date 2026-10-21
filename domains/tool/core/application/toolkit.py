"""工具集（``Toolkit``）。

一个 agent 能用哪些工具，全部收在本类里：注册 → 分组 → 出 schema → 被调用。
对外契约由 ``runtime.service.runtime_ports.ToolkitProtocol`` 描述
（``tools`` / ``get_json_schema`` / ``update_tool_groups`` / ``call_tool_function``），
本实现在此之上补齐了装配侧真实用到的入口
（``register_tool_function`` / ``create_tool_group`` / ``get_json_schemas`` …）。

两条设计约束：

* **注册永不因同名而崩**。装配流水线会重复跑（内置工具、MCP、技能、插件），
  同一个工具名出现多次是常态，用 ``namesake_strategy`` 显式决定行为，
  而不是让后注册者静默顶掉先注册者。
* **执行入口要能被「盲调用」**。运行侧是按名字试探式取入口的（见
  ``react_agent_tool_execution``：``async_execute_tool`` / ``execute_tool`` /
  ``call_tool`` / ``get_tool`` …一串候选名），所以这里把常见名字都实现为
  同一语义的别名，避免「工具注册了却调不动」。
"""

from __future__ import annotations

import collections.abc
import dataclasses
import inspect
import typing

JsonObject = dict[str, object]

#: ``register_tool_function`` 的同名处理策略。
NAMESAKE_SKIP = "skip"
NAMESAKE_REPLACE = "replace"
NAMESAKE_RAISE = "raise"
NAMESAKE_RENAME = "rename"


@dataclasses.dataclass
class ToolGroup:
    """一组工具（用于整体开关，例如「本轮只开 MCP 工具」）。"""

    name: str
    description: str = ""
    active: bool = True
    notes: str | None = None


def _json_type(annotation: object) -> str:
    """把 Python 注解粗略映射成 JSON Schema 的 ``type``。

    够用即可：工具参数绝大多数是标量 / 数组 / 对象，映射不出来就按
    ``"string"``——模型侧拿到的是「提示」而不是强约束。
    """
    if annotation is inspect.Signature.empty or annotation is None:
        return "string"
    if annotation is str:
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation is int or annotation is float:
        return "number"
    if annotation in (list, tuple, set):
        return "array"
    if annotation is dict:
        return "object"
    origin = typing.get_origin(annotation)
    if origin in (list, tuple, set, collections.abc.Sequence):
        return "array"
    if origin in (dict, collections.abc.Mapping):
        return "object"
    if origin is typing.Union or origin is getattr(collections.abc, "Union", None):
        return "string"
    return "string"


def _first_doc_line(func: object) -> str:
    doc = inspect.getdoc(func) if not isinstance(func, str) else func
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


class ToolRegistration:
    """一个已注册的工具。

    满足 ``RuntimeToolRegistrationProtocol``：暴露 ``is_agent`` 与 ``preset_kwargs``。
    """

    # ``is_agent`` / ``preset_kwargs`` 是 property（协议要求的可读属性），
    # 不能同时进 ``__slots__``——同名会触发
    # ``ValueError: 'is_agent' in __slots__ conflicts with class variable``。
    __slots__ = (
        "name",
        "func",
        "description",
        "group",
        "_is_agent",
        "_preset_kwargs",
        "active",
        "notes",
        "source",
    )

    def __init__(
        self,
        name: str,
        func: typing.Any,
        *,
        description: str = "",
        group: str | None = None,
        is_agent: bool = False,
        preset_kwargs: typing.Optional[collections.abc.Mapping[str, object]] = None,
        active: bool = True,
        notes: str | None = None,
        source: str = "",
    ) -> None:
        self.name = name
        self.func = func
        self.description = description or _first_doc_line(func)
        self.group = group
        self.is_agent = is_agent
        self.preset_kwargs: typing.Mapping[str, object] = dict(preset_kwargs or {})
        self.active = active
        self.notes = notes
        self.source = source

    # --- 协议面 --------------------------------------------------------
    @property
    def is_agent(self) -> bool:
        return self._is_agent

    @is_agent.setter
    def is_agent(self, value: bool) -> None:
        self._is_agent = bool(value)

    @property
    def preset_kwargs(self) -> typing.Mapping[str, object]:
        return self._preset_kwargs

    @preset_kwargs.setter
    def preset_kwargs(self, value: typing.Optional[collections.abc.Mapping[str, object]]) -> None:
        self._preset_kwargs = dict(value or {})

    # --- schema --------------------------------------------------------
    def parameters_schema(self) -> JsonObject:
        """从函数签名推一份 JSON Schema（``object`` 形态）。"""
        properties: dict[str, object] = {}
        required: list[str] = []
        try:
            signature = inspect.signature(self.func)
        except (TypeError, ValueError):
            return {"type": "object", "properties": properties, "required": required}

        for param_name, param in signature.parameters.items():
            if param_name in ("self", "cls"):
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            properties[param_name] = {"type": _json_type(param.annotation)}
            if param.default is param.empty:
                required.append(param_name)
        return {"type": "object", "properties": properties, "required": required}

    def json_schema(self) -> JsonObject:
        """OpenAI 形态的工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema(),
            },
        }

    # --- 执行 ----------------------------------------------------------
    async def call(self, arguments: typing.Optional[collections.abc.Mapping[str, object]] = None) -> object:
        """按参数调用工具；同步/异步函数都收。"""
        kwargs: dict[str, object] = dict(self.preset_kwargs)
        kwargs.update(dict(arguments or {}))
        produced = self.func(**kwargs)
        if inspect.isawaitable(produced):
            produced = await produced
        return produced

    def __repr__(self) -> str:
        return f"ToolRegistration(name={self.name!r}, is_agent={self.is_agent!r}, active={self.active!r})"


class Toolkit:
    """一个 agent 的工具集。"""

    def __init__(self) -> None:
        self.tools: dict[str, ToolRegistration] = {}
        self.groups: dict[str, ToolGroup] = {}
        # 装配侧写入的开关（沿用调用点既有拼写）；语义：未限定白名单时导出全部工具。
        self.expost_all_registered_tools: bool = False

    # --- 分组 ----------------------------------------------------------
    def create_tool_group(
        self,
        group_name: str,
        description: str = "",
        active: bool = True,
        notes: str | None = None,
    ) -> ToolGroup:
        """登记一个工具组；同名组直接复用（不覆盖已设的开关）。"""
        existing = self.groups.get(group_name)
        if existing is not None:
            return existing
        group = ToolGroup(name=group_name, description=description, active=active, notes=notes)
        self.groups[group_name] = group
        return group

    def update_tool_groups(self, group_names: list[str], active: bool) -> None:
        """批量开关工具组，并把开关同步到组内工具。

        组不存在时创建（声明顺序不保证），工具是否生效以组的 ``active`` 为准。
        """
        for group_name in group_names or ():
            group = self.groups.get(group_name)
            if group is None:
                group = self.create_tool_group(group_name, active=active)
            group.active = bool(active)
            for registration in self.tools.values():
                if registration.group == group_name:
                    registration.active = bool(active)

    # --- 注册 ----------------------------------------------------------
    def register_tool_function(
        self,
        func: typing.Any,
        *,
        name: str | None = None,
        description: str | None = None,
        group: str | None = None,
        namesake_strategy: str = NAMESAKE_SKIP,
        is_agent: bool = False,
        preset_kwargs: typing.Optional[collections.abc.Mapping[str, object]] = None,
        active: bool = True,
        notes: str | None = None,
        source: str = "",
    ) -> ToolRegistration | None:
        """注册一个工具函数。

        :param namesake_strategy: 同名时的处理：
            ``skip``（默认，保留先注册者并返回 ``None``）/ ``replace``（覆盖）/
            ``raise``（抛 ``ValueError``）/ ``rename``（追加序号后注册）。
        :return: 注册对象；被 ``skip`` 掉时返回 ``None``。
        """
        tool_name = name or getattr(func, "__name__", None) or str(func)
        if tool_name in self.tools:
            if namesake_strategy == NAMESAKE_SKIP:
                return None
            if namesake_strategy == NAMESAKE_RAISE:
                raise ValueError(f"tool {tool_name!r} is already registered")
            if namesake_strategy == NAMESAKE_RENAME:
                suffix = 2
                while f"{tool_name}-{suffix}" in self.tools:
                    suffix += 1
                tool_name = f"{tool_name}-{suffix}"

        registration = ToolRegistration(
            tool_name,
            func,
            description=description or "",
            group=group,
            is_agent=is_agent,
            preset_kwargs=preset_kwargs,
            active=active and self._group_allows(group),
            notes=notes,
            source=source,
        )
        self.tools[tool_name] = registration
        return registration

    def _group_allows(self, group: str | None) -> bool:
        if group is None:
            return True
        declared = self.groups.get(group)
        return True if declared is None else declared.active

    def unregister_tool(self, name: str) -> None:
        self.tools.pop(name, None)

    # --- schema --------------------------------------------------------
    def get_json_schema(self, active_only: bool = True) -> list[JsonObject]:
        """导出工具 schema 列表（OpenAI 形态）。"""
        return [
            registration.json_schema()
            for registration in self.tools.values()
            if registration.active or not active_only
        ]

    # 装配侧用的是复数形式，两个名字都留。
    get_json_schemas = get_json_schema

    def get_tool_schemas(self, active_only: bool = True) -> list[JsonObject]:
        """``get_json_schema`` 的别名（运行侧会按这个名字试探）。"""
        return self.get_json_schema(active_only=active_only)

    @property
    def tool_schemas(self) -> list[JsonObject]:
        return self.get_json_schema()

    @property
    def schemas(self) -> list[JsonObject]:
        return self.get_json_schema()

    def tool_names(self, active_only: bool = True) -> list[str]:
        return [
            name
            for name, registration in self.tools.items()
            if registration.active or not active_only
        ]

    # --- 取工具 --------------------------------------------------------
    def get_tool(self, name: str) -> ToolRegistration | None:
        return self.tools.get(name)

    # 运行侧按顺序试探 ``get_tool`` / ``get`` / ``tool`` / ``resolve``。
    get = get_tool
    tool = get_tool

    def resolve(self, name: str) -> ToolRegistration | None:
        return self.tools.get(name)

    def __getitem__(self, name: str) -> ToolRegistration:
        return self.tools[name]

    def __contains__(self, name: object) -> bool:
        return name in self.tools

    def __len__(self) -> int:
        return len(self.tools)

    def __iter__(self) -> typing.Iterator[str]:
        return iter(self.tools)

    # --- 执行 ----------------------------------------------------------
    async def execute_tool(
        self,
        name: str,
        arguments: typing.Optional[collections.abc.Mapping[str, object]] = None,
    ) -> object:
        """按名字执行工具；工具不存在抛 ``KeyError``。"""
        registration = self.tools.get(name)
        if registration is None:
            raise KeyError(f"tool {name!r} is not registered")
        return await registration.call(arguments)

    async def async_execute_tool(
        self,
        name: str,
        arguments: typing.Optional[collections.abc.Mapping[str, object]] = None,
    ) -> object:
        return await self.execute_tool(name, arguments)

    aexecute_tool = async_execute_tool
    call_tool = execute_tool
    invoke_tool = execute_tool
    run_tool = execute_tool
    ainvoke_tool = execute_tool

    async def call_tool_function(
        self,
        tool_call: typing.Any,
    ) -> typing.AsyncGenerator[object, None]:
        """协议要求的执行入口：吃一个 ``ToolUseBlock``，产出 ``ToolResponse`` 流。

        ``tool_call`` 既接受对象（``.name`` / ``.input``）也接受映射；
        ``ToolResponse`` 目前尚未落地（``runtime.model.tool_response`` 为空模块），
        所以能构造就构造，构造不了就退成普通映射——不让协议缺口拖垮调用链。
        """
        if isinstance(tool_call, collections.abc.Mapping):
            name = str(tool_call.get("name") or "")
            arguments = tool_call.get("input") or tool_call.get("arguments") or {}
            call_id = tool_call.get("id")
        else:
            name = str(getattr(tool_call, "name", "") or "")
            arguments = getattr(tool_call, "input", None)
            if arguments is None:
                arguments = getattr(tool_call, "arguments", None) or {}
            call_id = getattr(tool_call, "id", None)
        if not isinstance(arguments, collections.abc.Mapping):
            arguments = {"input": arguments}

        outcome = await self.execute_tool(name, dict(arguments))
        yield self._build_tool_response(name, call_id, outcome)

    @staticmethod
    def _build_tool_response(name: str, call_id: object, outcome: object) -> object:
        """把工具产出包成 ``ToolResponse``；包不了就退成映射。"""
        payload: JsonObject = {
            "tool_name": name,
            "tool_call_id": call_id,
            "output": outcome if isinstance(outcome, (str, int, float, bool, list, dict)) else str(outcome),
            "is_error": False,
        }
        try:
            import domains.agent.domain.value_objects.tool_response as tool_response_module

            response_cls = getattr(tool_response_module, "ToolResponse", None)
            if response_cls is not None:
                return response_cls.model_validate(payload)
        except Exception:
            pass
        return payload

    @property
    def export_all_registered_tools(self) -> bool:
        """``expost_all_registered_tools`` 的正确拼写别名。"""
        return self.expost_all_registered_tools

    @export_all_registered_tools.setter
    def export_all_registered_tools(self, value: bool) -> None:
        self.expost_all_registered_tools = bool(value)

    def __repr__(self) -> str:
        return f"Toolkit(tools={len(self.tools)}, groups={len(self.groups)})"
