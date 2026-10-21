import contextvars
import copy

import pydantic

import domains.agent.domain.value_objects.runtime_context as runtime_context


type RuntimeConfig =dict[str,pydantic.JsonValue]

_app_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("execution_app_id",default=None)

_runtime_config_snapshot: contextvars.ContextVar[RuntimeConfig|None]=contextvars.ContextVar(
    'execution_runtime_config',
     default=None)

def get_effective_runtime_config()->RuntimeConfig:
    snapshot =_runtime_config_snapshot.get()
    if snapshot is not None:
        return copy.deepcopy(snapshot)
    process_context = runtime_context.get_runtime_context_or_none()
    if process_context is None:
        return {}
    return copy.deepcopy(process_context.merged_config)


# 请求级 app 上下文：(app_id, project_home, app_instance)
_app_request_context: contextvars.ContextVar[tuple | None] = contextvars.ContextVar(
    "app_request_context",
    default=None,
)
_groupchat_round_instance_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "execution_groupchat_round_instance_id",
    default=None,
)
_thinking_operation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "execution_thinking_operation_id",
    default=None,
)

_cached_thinking_input: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "execution_cached_thinking_input",
    default=None,
)

_agent_scheduler: contextvars.ContextVar[object|None]=contextvars.ContextVar(
    "execution_agent_scheduler",
    default=None,
)
_parent_invocation_id:contextvars.ContextVar[object|None]=contextvars.ContextVar(
    "execution_parent_invocation_id",
    default=None,
)

_root_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("execution_root_agent_id",default=None)

_current_invocation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "execution_current_invocation_id",
    default=None,
)

_current_agent_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "execution_current_agent_name",
    default=None,
)

def get_session_id()->str | None:
    return get_app_id()

def get_root_agent_id()->str | None:
    return _root_agent_id.get()

def set_root_agent_id(agent_id:str |None) -> None:
    _root_agent_id.set(agent_id)


def bind_execution_context(
    app_id: str,
    project_home: object,
    app_instance: object | None = None,
) -> None:
    """绑定当前请求的 app 上下文到 contextvar，供协程内取回。"""
    _app_request_context.set((app_id, project_home, app_instance))


def get_execution_context() -> tuple | None:
    """获取当前绑定的 app 上下文 ``(app_id, project_home, app_instance)``；未绑定返回 None。"""
    return _app_request_context.get()


def get_app_id() -> str | None:
    """获取当前请求的 app id；未绑定上下文时返回 ``None``。

    作为 ``role_context_service`` 的 ``app_id_provider`` 使用，因此必须容忍
    「无请求上下文」这一正常情形（例如应用装配阶段、后台任务），不抛异常。
    """
    context = _app_request_context.get()
    if not context:
        return None
    app_id = context[0]
    return str(app_id) if app_id else None


def get_project_home() -> object | None:
    """获取当前请求绑定的 project_home；未绑定返回 ``None``。"""
    context = _app_request_context.get()
    if not context:
        return None
    return context[1]

def get_groupchat_round_instance_id()->str | None:
    return _groupchat_round_instance_id.get()

def set_current_thinking_operation_id(operation_id: str|None) -> None:
    _thinking_operation_id.set(operation_id)

def get_current_thinking_operation_id()->str|None:
    """取回当前 thinking operation 标识；未设置返回 ``None``。

    ``observability/.../runtime_observer_bridge.py`` 有 5 处调用此前依赖本函数，
    但只有 setter 存在，导致每次 on_thinking_start/end 都抛
    ``AttributeError: module 'runtime.service.execution_context' has no
    attribute 'get_current_thinking_operation_id'``。
    """
    return _thinking_operation_id.get()

def set_current_agent_name(agent_name: str | None) -> None:
    """记录当前正在执行工具调用的 agent 名。

    ``domains/agent/application/agent_tool_event_hooks.py`` 的 tool-start /
    tool-end 两个钩子都会调用它（start 写 ``agent.name``，end 写 ``None``
    复位），用于把工具事件关联到发起它的 agent。此前本模块**没有**这个函数，
    钩子一触发就抛 ``AttributeError: module 'runtime.service.execution_context'
    has no attribute 'set_current_agent_name'``，工具执行链被截断。
    """
    _current_agent_name.set(agent_name)

def get_current_agent_name() -> str | None:
    """取回当前 agent 名；未设置返回 ``None``。"""
    return _current_agent_name.get()

def get_current_parent_agent_instance_id()->str|None:
    return _parent_invocation_id.get()

def set_current_invocation_id(invocation_id: str | None) -> None:
    """记录当前调用链（invocation）标识，供 agent 生命周期钩子 / 调度器关联上下文。

    大量下游（``agent_scheduler``、``provider_chat_model``、``runtime_observer_bridge``
    等）都通过 :func:`get_current_invocation_id` 取回该标识；缺实现会让这些调用在
    LLM 真正发起前就抛 ``AttributeError``，导致整条 turn 失败。
    """
    _current_invocation_id.set(invocation_id)

def get_current_invocation_id() -> str | None:
    """取回当前 invocation 标识；未设置返回 ``None``。"""
    return _current_invocation_id.get()


def set_cached_thinking_input(value: object | None) -> None:
    """缓存当前请求的 thinking 输入（prompt 摘要等），供 ``on_thinking_start`` 取回。

    由 ``llm/provider/application/tool_argument_formatter.py`` 在消息格式化后置入；
    ``observability/infrastructure/observers/runtime_observer_bridge.py`` 的
    ``on_thinking_start`` 取回，用来填充 thinking 起始提示。两者处于同一次请求的
    contextvar 上下文，缺实现会让 ``on_thinking_start`` 在 LLM 真正发起前就抛
    ``AttributeError``，导致整条 turn 失败——与前文 ``set_current_invocation_id``
    注释里描述的「缺实现即 turn 失败」是同一类问题。传入 ``None`` 表示「无缓存」，
    调用方会回退到参数传入的 ``initial_prompt``。
    """
    _cached_thinking_input.set(value)


def get_cached_thinking_input() -> object | None:
    """取回当前请求的缓存 thinking 输入；未设置返回 ``None``。"""
    return _cached_thinking_input.get()

def get_agent_scheduler()->object|None:
    return _agent_scheduler.get()

def get_app_instance() -> object | None:
    """获取当前请求绑定的 app 实例；未绑定返回 ``None``。"""
    context = _app_request_context.get()
    if not context:
        return None
    return context[2]