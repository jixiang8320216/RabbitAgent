"""委派 agent 的管理工具。

``AgentToolkitBuilder`` 把这三个函数注册成普通工具，让模型能查询 / 等待 /
取消自己发出的委派：

* :func:`check_agent_status` —— 这个 agent 现在什么状态？
* :func:`await_agent_result` —— 等它跑完，把结果拿回来；
* :func:`cancel_agent` —— 不等了，取消。

设计取向：**全部返回可读文本，不抛异常**。它们是给模型用的工具，抛异常只会让
模型失去「换个方式重试」的机会；出错时返回 ``[check_agent_status] ...`` 这类
带前缀的说明，模型既能读懂也能据此决策。

当前委派链路（调度器 / 结果回收）尚未完全打通，所以这里的实现以
「能查到就说清楚，查不到就说清楚为什么」为准，不假装拿到了结果。
"""

from __future__ import annotations

import asyncio
import inspect
import typing

import domains.agent.domain.value_objects.tool_response as tool_response

__all__ = ["check_agent_status", "await_agent_result", "cancel_agent"]


def _resolve_app(app: typing.Any = None) -> typing.Any:
    """拿宿主应用：优先用调用方给的，其次走全局执行上下文。

    ``app.model.execution_context`` 目前仍是空模块，所以 ``get_app_instance``
    可能不存在——取不到就返回 ``None``，由调用方转成诊断文本。
    """
    if app is not None:
        return app
    try:
        import domains.configuration.domain.runtime_state.execution_context as execution_context_module

        getter = getattr(execution_context_module, "get_app_instance", None)
        if callable(getter):
            return getter()
    except Exception:  # noqa: BLE001 - 上下文缺席是常态
        return None
    return None


def _resolve_agent(app: typing.Any, agent_name: str) -> typing.Any:
    """按名字取 agent 运行时；取不到返回 ``None``。"""
    if not agent_name:
        return None
    runtimes = getattr(app, "agent_runtimes", None)
    if isinstance(runtimes, typing.Mapping):  # type: ignore[arg-type]
        found = runtimes.get(agent_name)
        if found is not None:
            return found
    getter = getattr(app, "get_agent_runtime", None)
    if callable(getter):
        try:
            return getter(agent_name)
        except Exception:  # noqa: BLE001
            return None
    return None


def _known_agent_names(app: typing.Any) -> tuple[str, ...]:
    runtimes = getattr(app, "agent_runtimes", None)
    if isinstance(runtimes, typing.Mapping):  # type: ignore[arg-type]
        return tuple(str(name) for name in runtimes.keys())
    return ()


def _status_of(agent: typing.Any) -> str:
    """从 agent 对象上读状态；读不到给个中性文案。"""
    for attribute in ("status", "state", "lifecycle_state"):
        value = getattr(agent, attribute, None)
        if value is None:
            continue
        text = getattr(value, "value", None) or getattr(value, "kind", None) or str(value)
        return str(text)
    if getattr(agent, "is_compressing", False):
        return "compressing"
    return "unknown"


async def check_agent_status(agent_name: str = "", *, name: str | None = None, **extra: typing.Any) -> str:
    """查询一个委派 agent 的当前状态。

    :param agent_name: 目标 agent 名（模型也可能写成 ``name``）。
    """
    target = (agent_name or name or "").strip()
    app = _resolve_app(extra.get("app"))
    if app is None:
        return "[check_agent_status] 宿主应用不可用"
    if not target:
        known = _known_agent_names(app)
        hint = f"已知的 agent：{', '.join(known)}" if known else "当前没有已知 agent"
        return f"[check_agent_status] 缺少 agent_name；{hint}"
    agent = _resolve_agent(app, target)
    if agent is None:
        known = _known_agent_names(app)
        hint = f"已知的 agent：{', '.join(known)}" if known else "当前没有已知 agent"
        return f"[check_agent_status] 未找到 agent {target!r}；{hint}"
    return f"[check_agent_status] {target}: {_status_of(agent)}"


async def await_agent_result(
    agent_name: str = "",
    timeout: float | int | str | None = None,
    *,
    name: str | None = None,
    **extra: typing.Any,
) -> str:
    """等待一个委派 agent 完成并返回它的结果。

    :param timeout: 等待上限（秒）。``None`` 表示用内置默认值（30 秒）——
        无限等待会把本轮对话挂死，所以这里必须有上限。
    """
    target = (agent_name or name or "").strip()
    app = _resolve_app(extra.get("app"))
    if app is None:
        return "[await_agent_result] 宿主应用不可用"
    if not target:
        known = _known_agent_names(app)
        hint = f"已知的 agent：{', '.join(known)}" if known else "当前没有已知 agent"
        return f"[await_agent_result] 缺少 agent_name；{hint}"

    agent = _resolve_agent(app, target)
    if agent is None:
        return f"[await_agent_result] 未找到 agent {target!r}"

    seconds = _coerce_timeout(timeout, default=30.0)
    entry = _result_entry(agent)
    if entry is None:
        # 结果回收接口尚未落地：如实说明，不要编一个结果出来。
        return (
            f"[await_agent_result] agent {target!r} 未提供结果回收接口"
            f"（当前状态：{_status_of(agent)}）"
        )
    try:
        produced = entry()
    except TypeError:
        return f"[await_agent_result] agent {target!r} 的结果接口签名不匹配"
    if inspect.isawaitable(produced):
        try:
            produced = await asyncio.wait_for(produced, timeout=seconds)
        except asyncio.TimeoutError:
            return f"[await_agent_result] 等待 {target!r} 超时（{seconds:g}s）"
    return _as_text(produced)


async def cancel_agent(agent_name: str = "", *, name: str | None = None, **extra: typing.Any) -> str:
    """取消一个正在跑的委派 agent。"""
    target = (agent_name or name or "").strip()
    app = _resolve_app(extra.get("app"))
    if app is None:
        return "[cancel_agent] 宿主应用不可用"
    if not target:
        known = _known_agent_names(app)
        hint = f"已知的 agent：{', '.join(known)}" if known else "当前没有已知 agent"
        return f"[cancel_agent] 缺少 agent_name；{hint}"
    agent = _resolve_agent(app, target)
    if agent is None:
        return f"[cancel_agent] 未找到 agent {target!r}"

    for attribute in ("cancel", "cancel_run", "interrupt", "stop"):
        entry = getattr(agent, attribute, None)
        if not callable(entry):
            continue
        try:
            produced = entry()
        except TypeError:
            continue
        if inspect.isawaitable(produced):
            await produced
        return f"[cancel_agent] 已请求取消 {target!r}"

    manager = getattr(app, "interrupt_manager", None)
    canceller = getattr(manager, "cancel", None) or getattr(manager, "interrupt", None)
    if callable(canceller):
        try:
            produced = canceller(target)
            if inspect.isawaitable(produced):
                await produced
            return f"[cancel_agent] 已请求取消 {target!r}"
        except Exception as exc:  # noqa: BLE001
            return f"[cancel_agent] 取消 {target!r} 失败：{exc}"
    return f"[cancel_agent] agent {target!r} 没有可调用的取消入口"


def _coerce_timeout(value: typing.Any, *, default: float) -> float:
    """把超时入参转成秒；转不了用默认值，且不允许负数。"""
    if value is None:
        return default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return seconds if seconds > 0 else default


def _result_entry(agent: typing.Any) -> typing.Any:
    """取 agent 的结果回收入口；没有返回 ``None``。"""
    for attribute in ("result", "get_result", "await_result", "result_text", "get_result_text"):
        entry = getattr(agent, attribute, None)
        if callable(entry):
            return entry
    return None


def _as_text(produced: typing.Any) -> str:
    if isinstance(produced, str):
        return produced
    for attribute in ("content", "text", "message"):
        value = getattr(produced, attribute, None)
        if isinstance(value, str):
            return value
    return str(produced)

async def check_agent_status(agent_id:str|None=None)->tool_response.ToolResponse:
    ...