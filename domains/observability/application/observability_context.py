"""遥测上下文：进程级标识 + 查询级状态。

分两类：

- **进程级**（模块级变量）：入口点、进程属主、网络区域。
  这些在一进程内恒定，由 ``initialize_telemetry`` 装配时写入。
- **查询级**（``contextvars``）：trace id、应用模式、会话类型、查询起点、
  agent 调用次数。一次查询内的所有埋点共享这些维度。

为什么用 ``contextvars``：并发查询（多个会话同时跑）时，每个查询需要
独立的上下文。``contextvars`` 天然随协程/任务隔离，比全局变量安全。

**历史缺口**：
- ``configure_process_owner`` / ``set_network_zone`` / ``set_entry_point`` /
  ``get_hook_turn_id`` / ``increment_tool_call_count`` / ``get_trace_id``
  此前都不存在，而 ``composition`` 与 ``emitter`` 一直在调用它们。
- 文件里 ``_app_mode`` / ``_session_type`` / ``_query_start_time``
  与 ``set_app_mode`` / ``set_session_type`` / ``set_query_start_time`` /
  ``reset_query_stats`` 各被定义了两遍 —— 后定义的覆盖先定义的，
  签名还不一致。这里合并为单一实现。
"""

from __future__ import annotations

import contextvars
import secrets
import time

# ---------------------------------------------------------------------------
# 进程级上下文
# ---------------------------------------------------------------------------

#: 应用入口点（cli / web / weblink），由 initialize_telemetry 写入
_entry_point: str | None = None
#: 进程属主标识，用于区分「谁」在产生遥测
_process_owner: str | None = None
#: 网络区域标识
_network_zone: str | None = None


def set_entry_point(entry_point: str | None) -> None:
    """记录应用入口点。"""
    global _entry_point
    _entry_point = entry_point


def get_entry_point() -> str | None:
    """取应用入口点。"""
    return _entry_point


def configure_process_owner(process_owner: str | None) -> None:
    """配置进程属主。

    属主用于区分「同一套部署里哪个进程实例」产生的遥测 ——
    容器化部署时 pod 名 / 主机名比 pid 更有意义。
    """
    global _process_owner
    _process_owner = process_owner


def get_process_owner() -> str | None:
    """取进程属主。"""
    return _process_owner


def set_network_zone(network_zone: str | None) -> None:
    """记录网络区域。"""
    global _network_zone
    _network_zone = network_zone


def get_network_zone() -> str | None:
    """取网络区域。"""
    return _network_zone


# ---------------------------------------------------------------------------
# 查询级上下文
# ---------------------------------------------------------------------------

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_app_mode: contextvars.ContextVar[str | None] = contextvars.ContextVar("app_mode", default=None)
_session_type: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "session_type", default=None
)
_query_start_time: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "query_start_time", default=None
)
_hook_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hook_turn_id", default=None
)
_agent_invoke_count: contextvars.ContextVar[int] = contextvars.ContextVar(
    "agent_invoke_count", default=0
)
_tool_call_count: contextvars.ContextVar[int] = contextvars.ContextVar(
    "tool_call_count", default=0
)


def set_trace_id(trace_id: str | None) -> None:
    """设置当前 trace id。"""
    _trace_id.set(trace_id)


def get_trace_id() -> str | None:
    """取当前 trace id。"""
    return _trace_id.get()


def generate_trace_id() -> str:
    """生成一个新的 trace id。"""
    return f"tr_{secrets.token_hex(8)}"


def set_app_mode(app_mode: str | None) -> None:
    """记录本次查询的应用模式（DELEGATE / PLAN 等），仅作维度记录。"""
    _app_mode.set(app_mode)


def get_app_mode() -> str | None:
    """取本次查询的应用模式。"""
    return _app_mode.get()


def set_session_type(session_type: object | None) -> None:
    """记录本次查询的会话类型，仅作维度记录。"""
    _session_type.set(session_type)


def get_session_type() -> object | None:
    """取本次查询的会话类型。"""
    return _session_type.get()


def set_query_start_time(start_time: float | None = None) -> None:
    """记录查询起点；不传参时取当前单调时钟。"""
    if start_time is None:
        start_time = time.monotonic()
    _query_start_time.set(start_time)


def get_query_start_time() -> float | None:
    """取查询起点（单调时钟秒）。"""
    return _query_start_time.get()


def set_hook_turn_id(turn_id: str | None) -> None:
    """记录当前 hook 轮次 id。"""
    _hook_turn_id.set(turn_id)


def get_hook_turn_id() -> str | None:
    """取当前 hook 轮次 id。"""
    return _hook_turn_id.get()


def increment_agent_invoke_count(step: int = 1) -> int:
    """累加本次查询里 agent 的调用次数，返回累加后的值。"""
    current = _agent_invoke_count.get() + step
    _agent_invoke_count.set(current)
    return current


def get_agent_invoke_count() -> int:
    """取本次查询里 agent 的调用次数。"""
    return _agent_invoke_count.get()


def increment_tool_call_count(step: int = 1) -> int:
    """累加本次查询里工具的调用次数，返回累加后的值。"""
    current = _tool_call_count.get() + step
    _tool_call_count.set(current)
    return current


def get_tool_call_count() -> int:
    """取本次查询里工具的调用次数。"""
    return _tool_call_count.get()


def reset_agent_invoke_count() -> int:
    """把 agent 调用计数清零并返回清零后的值（通常是 ``0``）。

    ``domains/observability/__init__.py`` 的导出表里早就登记了这个名字，但本模块
    此前没有它——登记了一个不存在的导出，调用方只有在
    ``from domains.observability import reset_agent_invoke_count`` 那一刻才会
    ``AttributeError``。与其删掉这条导出，不如把它补上：计数是本模块自己的状态，
    清零是对同一状态的合法操作，放在这里比让调用方去 ``reset_query_stats()``
    （那会连开始时间一起清掉）语义更准。
    """
    _agent_invoke_count.set(0)
    return _agent_invoke_count.get()


def reset_query_stats() -> None:
    """清空上一轮的查询统计，准备新一轮。"""
    _query_start_time.set(None)
    _agent_invoke_count.set(0)
    _tool_call_count.set(0)


__all__ = [
    "configure_process_owner",
    "generate_trace_id",
    "get_agent_invoke_count",
    "get_app_mode",
    "get_entry_point",
    "get_hook_turn_id",
    "get_network_zone",
    "get_process_owner",
    "get_query_start_time",
    "get_session_type",
    "get_tool_call_count",
    "get_trace_id",
    "increment_agent_invoke_count",
    "increment_tool_call_count",
    "reset_agent_invoke_count",
    "reset_query_stats",
    "set_app_mode",
    "set_entry_point",
    "set_hook_turn_id",
    "set_network_zone",
    "set_query_start_time",
    "set_session_type",
    "set_trace_id",
]
