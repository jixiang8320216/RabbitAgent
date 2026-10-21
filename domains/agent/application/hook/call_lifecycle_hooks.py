"""agent 调用（agent call）生命周期 hook 的挂载器。

一次「agent 调用」= 某个 agent 被交给一段 task 去跑（典型是主 agent 调子 agent，
或直接跑 root agent）。这类调用的两端要各发一次事件：

- 开始：``on_agent_call_start(agent_name, task, instance_id, parent_id, agent)``
- 结束：``on_agent_call_end(agent_name, end_timestamp, error, instance_id, parent_id)``

本类负责把这两端**挂上去**，分两步走：

1. ``_run_start`` —— 先跑**宿主自己的 hook**（``host._fire_hooks`` 或
   ``host._hooks`` 字典），让 hook 有机会改写/补充 payload；
2. ``_notify_start`` / ``_notify_end`` —— 再把事件发给 observer。

``_flush_final_reponse`` 是结束时的收尾：把最终回复文本**一次性**推给
observer（``on_agent_text_stream``）并用 ``on_agent_text_stream_end`` 收口，
让 TUI 侧能把挂着的 live 区定稿（``_finalize_agent_response_once``）。

设计约束（重要）：

- **一切失败都只记日志**。hook / observer 是观测性的，observer 甚至可能根本
  不存在（非 TUI 场景），绝不能因为观测失败打断一轮对话。
- **observer 形状不定**。它可能是 ``TUIObserver``，也可能是空实现或代理对象，
  方法签名可能缺少 ``instance_id`` 这类可选参数。所以调用按「完整关键字 →
  逐级降级」重试，遇到 ``TypeError``（多半是签名不匹配）就减参数再来。
"""

from __future__ import annotations

import asyncio
import collections.abc as abc
import datetime as datetime_module
import logging


import typing

import domains.events.domain.event_type as event_type
# AgentCallState 定义在 model 层（``agent.model.agent_call_state``），与
# ``agent_call_context`` 用的是同一份；service 层没有这个模块，按原路径 import
# 只会拿到一个空模块，进而在构造状态时抛 AttributeError。
import domains.agent.domain.entities.agent_call_state as agent_call_state
import domains.agent.application.context.call_context as agent_call_context
import domains.agent.application.lifecycle.invocation_tracker as agent_invocation_tracker
from domains.agent.domain.aggregates import execution_context
from domains.observability.application import observability_context
from domains.observability.domain import rabbit_observer
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.agent.domain.value_objects.message as message
import domains.agent.application.hook.runtime_hook_contracts as hook_contracts
import domains.observability.application.emitter as emitter
import domains.agent.application.hook.runtime_hook_support as hook_support
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
from domains.agent.domain.aggregates import scope_context

logger = logging.getLogger(__name__)

__all__ = ["AgentCallLifecycleHookAttacher"]

class GenerateResponseDecision(typing.Protocol):
    def __call__(
        self,
        agent_name:str,
        is_root_invocation:bool |None=None,
    )->bool:...


def _resolve_instance_id(agent:agent_module.ReActAgent,*, is_root_invocation: bool)->str:
    groupchat_round_id = execution_context.get_groupchat_round_instance_id()
    if groupchat_round_id:
        return groupchat_round_id
    if is_root_invocation:
        instance_id = agent_invocation_tracker.AgentInvocationTracker().generate_instance_id(agent.name)
        execution_context.set_current_invocation_id(instance_id)
        return instance_id
    invocation_id = execution_context.get_current_invocation_id()
    if invocation_id:
        return invocation_id
    instance_id = agent_invocation_tracker.AgentInvocationTracker().generate_instance_id(agent.name)
    execution_context.set_current_invocation_id(instance_id)
    return instance_id
def _user_message(value:object)->str:
    if value is None:
        return "No message"
    if isinstance(value,message.Msg):
        return value.get_text_content() or "No message"
    return str(value)



class AgentCallLifecycleHookAttacher:
    """把一次 agent 调用的 start / end 挂到宿主 hook 与 observer 上。"""

    def __init__(
        self,
        *,
        session_id_resolver: abc.Callable[[], str | None],
        observer_instance: rabbit_observer.RabbitObserver | None,
        app_resolver: abc.Callable[[],object|None],
        parent_instance_resolver: abc.Callable[[], object | None],
        interrupt_checker_resolver: abc.Callable[[], runtime_ports.InterruptCheckerProtocol | None],
        generate_response_decision: GenerateResponseDecision,
    ) -> None:
        self._session_id_resolver = session_id_resolver
        self._observer_instance = observer_instance
        self._app_resolver = app_resolver
        self._parent_instance_resolver = parent_instance_resolver
        self._interrupt_checker_resolver = interrupt_checker_resolver
        self._generate_response_decision = generate_response_decision

    def attach_start(
        self,
        agent: agent_module.ReActAgent,
        *,
        is_root_agent: bool
    ) -> None:
        async def send_agent_call_start_hook(
            agent_self:agent_module.ReActAgent,
            kwargs:dict[str,object],
        )->None:
            del agent_self
            await  self._run_start(agent, kwargs, is_root_agent=is_root_agent)
        agent.register_instance_hook("pre_reply","send_agent_call_start", send_agent_call_start_hook)


    def attach_end(
        self,
        agent: agent_module.ReActAgent,
        *,
        is_root_agent: bool
    ) -> None:
        async def send_agent_call_end_hook(
            agent_self:agent_module.ReActAgent,
            kwargs:dict[str,object],
        )->None:
            del agent_self
            await  self._run_end(agent, kwargs, is_root_agent=is_root_agent)
        agent.register_instance_hook("post_reply","send_agent_call_end", send_agent_call_end_hook)

    async def _run_start(
        self,
        agent: agent_module.ReActAgent,
        kwargs: dict[str, object],
        *,
        is_root_agent: bool,
    ) -> None:
        parent_id, is_root_invocation = self._parent_identity()
        instance_id = _resolve_instance_id(
            agent,
            is_root_invocation=is_root_invocation,
        )
        user_message = _user_message(kwargs.get("msg"))
        app_instance = self._app_resolver()
        _bind_invocation_start(
            agent,
            app_instance,
            instance_id,
            is_root_invocation=is_root_invocation,
        )
        await self._notify_start(agent, user_message, instance_id, parent_id)
        _emit_start_telemetry(
            agent,
            user_message,
            parent_id,
            is_root_agent=is_root_agent,
        )
        agent_call_context.set_agent_call_state(
            agent_call_state.AgentCallState(
                instance_id=instance_id,
                parent_id=parent_id,
                is_root_invocation=is_root_invocation,
                user_message=user_message,
                start_time=datetime_module.datetime.now(datetime_module.UTC),
            )
        )

    async def _run_end(
                self,
                agent: agent_module.ReActAgent,
                kwargs: dict[str, object],
                *,
                is_root_agent: bool,
        ) -> None:
            state = agent_call_context.get_agent_call_state()
            if state is None:
                return
            await self._flush_final_response(agent, state)
            execution_context.set_current_thinking_operation_id(None)
            agent_error = self._agent_error(agent,kwargs.get("exception"))
            end_timestamp= datetime_module.datetime.now(datetime_module.UTC)
            await self._notify_end(agent,state,end_timestamp,agent_error)
            app_instance = self._app_resolver()
            _bind_invocation_end(agent, app_instance,end_timestamp)
            _emit_end_telemetry(
                agent,
                app_instance,
                state,
                end_timestamp,
                agent_error,
                is_root_agent=is_root_agent
            )
            if state.is_root_invocation:
                _record_root_invocation(app_instance,state,end_timestamp,agent_error)
            agent_call_context.set_agent_call_state(None)
            _log_agent_completion(agent, agent_error)

    async def _notify_start(
        self,
        agent: agent_module.ReActAgent,
        user_message: str,
        instance_id: str,
        parent_id: str,
    ) -> None:
        """把开始事件发给 observer（``on_agent_call_start``）。"""
        observer = self._observer()
        if observer is None:
            return
        try:
            await observer.on_agent_call_start(
                agent_name=agent.name,
                task=user_message,
                instance_id=instance_id,
                parent_id=parent_id,
                agent=agent,
            )
        except Exception as e:
            logger.error(e)

    async def _notify_end(
        self,
        agent: agent_module.ReActAgent,
        state: agent_call_state.AgentCallState,
        end_timestamp: datetime_module.datetime,
        agent_error: str | None,
    ) -> None:
        """把结束事件发给 observer（``on_agent_call_end``）。"""
        observer= self._observer()
        if observer is None:
            return
        try:
            await observer.on_agent_call_end(
                agent_name=agent.name,
                end_timestamp=end_timestamp.isoformat(),
                error=agent_error,
                instance_id= state.instance_id,
                parent_id= state.parent_id,
            )
        except Exception as e:
            logger.error(e)

    async def _flush_final_response(
        self,
        agent: agent_module.ReActAgent,
        state: agent_call_state.AgentCallState,
    ) -> None:
        """把最终回复一次性刷给 observer 并收口。

        先 ``on_agent_text_stream(agent_name, text)`` 把正文推过去（observer 内部
        会按 ``<think>`` 边界分流到思考区/回复区），再
        ``on_agent_text_stream_end(agent_name)`` 让 TUI 把手上的 live 区定稿。

        ``final_response`` 形状不定（``str`` / ``Msg`` / 带 ``content`` 的对象 /
        ``{"content": ...}``），这里统一取文本；取不到文本就只做收口。
        """
        final_content = agent.take_last_response_text()
        if not final_content:
            return
        try:
            observer= self._observer()
            if observer is None:
                return
            if not self._generate_response_decision(
                agent.name,
                is_root_invocation= state.is_root_invocation,
            ):
                return
            await observer.on_generate_response(
                agent.name,
                final_content,
                is_final=True,
                instance_id=execution_context.get_current_invocation_id(),
            )
        except Exception as e:
            logger.error(e)

    # ------------------------------------------------------------------
    # 上下文解析（observer / 父身份 / 错误）
    # ------------------------------------------------------------------

    def _observer(self) -> rabbit_observer.RabbitObserver | None:
        """取这次调用要上报的 observer；拿不到返回 ``None``（即不发事件）。

        两级取值：先用在构造时直接注入的 ``observer_instance``；没有就退回
        ``app_resolver()``（再退 ``execution_context.get_app_instance()``）拿到的
        app 实例，并按 ``observer`` → ``base_observer`` 的顺序探属性。

        observer 是**观测性依赖**：非 TUI 场景下可能压根不存在，缺了只能是不发
        事件，绝不能影响一轮对话，所以全程只记 debug。
        """
        if self._observer_instance is not None:
            return self._observer_instance
        app_instance = self._app_resolver()
        if isinstance(app_instance, hook_contracts.ObserverApplication):
            return app_instance.observer
        return None


    def _parent_identity(self) -> tuple[str, bool]:
        parent_from_context = self._parent_instance_resolver()
        if parent_from_context is None:
            return event_type.SYSTEM_ROOT_ID, True
        return parent_from_context, False

    def _agent_error(
        self,
        agent: agent_module.ReActAgent,
        exception: object,
    ) -> str | None:
        active_scope= scope_context.get_turn_scope_context()
        if active_scope is not None and active_scope.cancelled:
            return "Task interrupted by user"
        session_id= self._session_id_resolver()
        checker= self._interrupt_checker_resolver()
        if session_id and checker is not None and checker.check_interrupt(session_id):
            return "Task interrupted by user"
        # 没有异常就是成功，返回 None（=无错误）。
        # 原实现把这两个分支写反了：``exception is not None`` 时返回 None，
        # 而 ``exception is None`` 时执行 ``str(None)[:300]`` 得到字符串 "None"。
        # 结果每一次正常跑完的调用都被判成 "Agent end with error : <name>- None"，
        # TUI 走on_agent_call_end 的 error 分支渲染成 "failed(1639ms)"，
        # 真正的模型答案反而被这条失败提示顶掉——这就是屏上看不到答案的原因。
        if exception is None:
            return None
        return str(exception)[:300]

def _bind_invocation_start(
    agent: agent_module.ReActAgent,
    app_instance: object,
    instance_id: str,
    *,
    is_root_invocation: bool,
) -> None:
    if is_root_invocation:
        execution_context.set_root_agent_id(agent.name)
        if isinstance(app_instance, hook_contracts.InvocationStateApplication):
            execution_state = app_instance.execution_context
            if execution_state is not None:
                execution_state.current_entry_agent_instance_id = instance_id
    if not isinstance(app_instance, hook_contracts.AgentTreeApplication):
        return
    try:
        node = app_instance.agent_tree.get(agent.name)
        # AgentNode 是纯数据模型（只有 is_root()），并未实现
        # mark_invocation_start/finished。调用它们会抛
        # "'AgentNode' object has no attribute 'mark_invocation_start'"。
        # 该标记属旁路观测，未实现时安静跳过即可。
        marker = getattr(node, "mark_invocation_start", None) if node is not None else None
        if marker is not None:
            marker(instance_id)
    except Exception as exc:
        logger.error(exc)



def _emit_start_telemetry(
    agent: agent_module.ReActAgent,
    app_instance: object| None,
    parent_id: str ,
    *,
    is_root_agent: bool ,
) -> bool:
    try:
        skill_name, role = hook_support.determine_skill_and_role(agent, app_instance, is_root_agent)
        observability_context.increment_agent_invoke_count()
        emitter.emit_agent_start(
            agent_id=agent.name,
            agent_name=agent.name,
            skill_name=skill_name,
            role=role,
            parent_id=parent_id if parent_id != event_type.SYSTEM_ROOT_ID else None,
        )
    except Exception as exc:
        logger.error(exc)

def _emit_end_telemetry(
    agent: agent_module.ReActAgent,
    app_instance: object| None,
    state: agent_call_state.AgentCallState,
    end_timestamp: datetime_module.datetime,
    agent_error: str |None,
    *,
    is_root_agent: bool ,
) -> bool:
    try:
        duration_ms = int((end_timestamp -state.start_time).total_seconds()*1000)
        skill_name, role = hook_support.determine_skill_and_role(agent, app_instance, is_root_agent)
        emitter.emit_agent_finish(
            agent_id=agent.name,
            agent_name=agent.name,
            skill_name=skill_name,
            role=role,
            parent_id=state.parent_id if state.parent_id != event_type.SYSTEM_ROOT_ID else None,
            status="error" if agent_error else "ok",
            tool_call_count=0,
            duration_ms=duration_ms,
            error_type=agent_error,
        )
    except Exception as exc:
        logger.error(exc)

def _bind_invocation_end(
    agent: agent_module.ReActAgent,
    app_instance: object|None,
    end_timestamp: datetime_module.datetime,
) -> None:
    if not isinstance(app_instance, hook_contracts.AgentTreeApplication):
        return
    try:
        node= app_instance.agent_tree.get(agent.name)
        # 同上：AgentNode 未实现 mark_invocation_finished，能力探测后跳过。
        marker = getattr(node, "mark_invocation_finished", None) if node is not None else None
        if marker is not None:
            marker(completed_at=end_timestamp)
    except Exception as exc:
        logger.error(exc)


def _record_root_invocation(
    app_instance: object,
    state: agent_call_state.AgentCallState,
    end_timestamp: datetime_module.datetime,
    agent_error: str | None,
) ->  None:
    execution_state=(
        app_instance.execution_context if isinstance(app_instance, hook_contracts.InvocationStateApplication) else None
    )
    if execution_state is not None:
        execution_state.plan_agent_invocations.append(
            {
                "instance_id": state.instance_id,
                "started_at": state.start_time.isoformat(),
                "ended_at": end_timestamp.isoformat(),
                "user_message": state.user_message,
                "status":"error" if agent_error else "completed"
            }
        )
        execution_state.current_entry_agent_instance_id =None
    execution_context.set_root_agent_id(None)
    execution_context.set_current_invocation_id(None)



def _log_agent_completion(
    agent: agent_module.ReActAgent,
    agent_error: str | None,
) -> None:
    if agent_error:
        logger.warning("Agent end with error : %s- %s", agent.name, agent_error)
    else:
        logger.info("Aggent end successfully: %s", agent.name)


# ----------------------------------------------------------------------
# ``_obsever`` / ``_parent_identity`` / ``_agent_error`` 的私有支撑。
#
# 全部放在文件末尾：它们只被上面三个方法在**调用时**消费，不参与模块装配，
# 因此定义在后面不影响导入；集中放一段也方便一眼看清「哪些是纯取值降级」。
# ----------------------------------------------------------------------

# app 实例上 observer 的候选属性名（宿主形状不定，逐个探）
_OBSERVER_ATTRS: tuple[str, ...] = ("observer", "base_observer")
# ``event_type.SYSTEM_ROOT_ID`` 尚未落地时的根父哨兵（名字对齐该常量语义）
_ROOT_PARENT_ID_FALLBACK = "system_root"
# 错误摘要的截断长度（对齐 ``agent_invocation_tracker._ERROR_PREVIEW_CHARS``）
_ERROR_PREVIEW_CHARS = 300
# 被中断的统一错误标签（对齐 ``agent_task_complete_hooks._error_summary``）
_INTERRUPTED_ERROR_TEXT = "turn_cancelled"


def _resolve_app_instance(
    resolver: abc.Callable[[], object | None] | None,
) -> object | None:
    """取 app 实例：先问注入的 resolver，再退 ``execution_context.get_app_instance()``。"""
    if callable(resolver):
        try:
            app_instance = resolver()
        except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
            logger.debug("[AGENT_CALL_HOOK] app_resolver failed: %s", exc)
        else:
            if app_instance is not None:
                return app_instance
    getter = getattr(execution_context, "get_app_instance", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
        logger.debug("[AGENT_CALL_HOOK] get_app_instance failed: %s", exc)
        return None


def _identifier_of(value: object) -> str | None:
    """把父实例的任意形状归一成非空字符串；归一不出来返回 ``None``。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    for attr in ("instance_id", "id", "agent_id", "instanceId"):
        try:
            candidate = getattr(value, attr, None)
        except Exception:  # noqa: BLE001 - 代理对象的 __getattr__ 可能抛
            continue
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _resolve_parent_id(
    resolver: abc.Callable[[], object | None] | None,
) -> str | None:
    """解析父实例 id：先问注入的 resolver，再退上下文里的同名 getter。"""
    if callable(resolver):
        try:
            parent_id = _identifier_of(resolver())
        except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
            logger.debug("[AGENT_CALL_HOOK] parent_instance_resolver failed: %s", exc)
        else:
            if parent_id is not None:
                return parent_id
    getter = getattr(execution_context, "get_current_parent_agent_instance_id", None)
    if not callable(getter):
        return None
    try:
        return _identifier_of(getter())
    except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
        logger.debug("[AGENT_CALL_HOOK] get_current_parent_agent_instance_id failed: %s", exc)
        return None


def _root_parent_id() -> str:
    """根调用的父 id（哨兵）；``event_type`` 还没落地该常量时用兜底值。"""
    root_id = getattr(event_type, "SYSTEM_ROOT_ID", None)
    if isinstance(root_id, str) and root_id:
        return root_id
    return _ROOT_PARENT_ID_FALLBACK


def _format_error(value: object) -> str:
    """把异常（或任意错误载体）压成一句短摘要；被中断的走 ``_INTERRUPTED_ERROR_TEXT``。"""
    if isinstance(value, (asyncio.CancelledError, KeyboardInterrupt)):
        return _INTERRUPTED_ERROR_TEXT
    if isinstance(value, BaseException):
        name = type(value).__name__
        try:
            detail = str(value).strip()
        except Exception:  # noqa: BLE001 - __str__ 可能自己抛
            detail = ""
        summary = f"{name}: {detail}" if detail else name
    elif isinstance(value, str):
        summary = value.strip() or "error"
    else:
        detail = ""
        for attr in ("message", "error", "detail"):
            try:
                candidate = getattr(value, attr, None)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(candidate, str) and candidate.strip():
                detail = candidate.strip()
                break
        if not detail:
            try:
                detail = str(value).strip()
            except Exception:  # noqa: BLE001
                detail = ""
        summary = f"{type(value).__name__}: {detail}" if detail else type(value).__name__
    if len(summary) > _ERROR_PREVIEW_CHARS:
        return summary[: _ERROR_PREVIEW_CHARS] + "..."
    return summary


def _interrupted(
    checker_resolver: abc.Callable[[], object | None] | None,
    session_resolver: abc.Callable[[], str | None] | None,
) -> bool:
    """问中断检查器这一轮是不是被掐断的；问不出来一律按「没中断」。

    ``InterruptCheckerProtocol.check_interrupt`` 要 ``session_id``，拿不到就直接
    放弃——**宁可漏报，也不能把正常结束误记成 error**。
    """
    if not callable(checker_resolver):
        return False
    try:
        checker = checker_resolver()
    except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
        logger.debug("[AGENT_CALL_HOOK] interrupt_checker_resolver failed: %s", exc)
        return False
    if checker is None or not callable(session_resolver):
        return False
    try:
        session_id = session_resolver()
    except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
        logger.debug("[AGENT_CALL_HOOK] session_id_resolver failed: %s", exc)
        return False
    if not isinstance(session_id, str) or not session_id:
        return False
    check = getattr(checker, "check_interrupt", None)
    if not callable(check):
        return False
    try:
        return bool(check(session_id))
    except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
        logger.debug("[AGENT_CALL_HOOK] check_interrupt failed: %s", exc)
        return False
