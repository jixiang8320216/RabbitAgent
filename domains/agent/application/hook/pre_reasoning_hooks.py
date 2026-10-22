from __future__ import annotations

import asyncio
import collections.abc
import collections.abc as abc
import datetime as datetime_module
import inspect
import logging
import typing
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
import domains.observability.application.emitter as emitter
import domains.agent.domain.value_objects.message as message
import domains.agent.application.context.queued_context as agent_queued_context
import domains.agent.domain.value_objects.conversation_context as conversation_context
import domains.agent.domain.entities.agent_context as agent_context_module
from domains.agent.infrastructure import file_agent_registry

if typing.TYPE_CHECKING:
    # 这三个**只用于注解**。本模块开了 ``from __future__ import annotations``，
    # 注解不求值，所以放到 TYPE_CHECKING 里是安全的——而且必须这么做：
    # ``app.protocols.config_protocol`` 现在 import 会 ``No module named 'model'``，
    # ``runtime.service.react_agent`` 会撞上 ``agent/__init__.py`` 的循环导入。
    # 真在顶层 import 它们，本模块（以及将来 import 它的 hook builder）会连带挂掉。
    import domains.configuration.domain.protocol.config_protocol as config_protocol
    import domains.observability.domain.rabbit_observer as rabbit_observer
    import domains.agent.domain.aggregates.react_agent as agent_module

logger = logging.getLogger(__name__)

__all__ = ["AgentPreReasoningHookAttacher"]


# 宿主 hook 的事件名与回调名。``register_instance_hook`` 对未注册的事件会抛
# ``ValueError``（``pre_reasoning`` 在 ``ReActAgent.HOOK_TYPES`` 里，是安全的）。
_HOOK_EVENT = "pre_reasoning"
_HOOK_NAME = "send_pre_reasoning_event"

# 打点表的上限：只用于算耗时，超出丢最老的一条，避免长会话里无限增长。
_MAX_TRACKED_STARTS = 64

# ``emitter`` 模块还没落地，遥测只是「尽力而为」：按这些候选名依次试，都没有就跳过。
_TELEMETRY_CANDIDATES: tuple[str, ...] = (
    "emit_agent_reasoning_start",
    "emit_reasoning_start",
    "emit_agent_reasoning",
)

# ``on_agent_reasoning_start(agent_name, thought)`` 的第二个参数。推理**前**还没有
# 任何思考内容，这里尽力取「模型即将要处理的东西」——找不到就给空串。
_THOUGHT_ATTRS: tuple[str, ...] = (
    "_last_user_message",
    "last_user_message",
    "_current_task",
    "current_task",
    "_last_msg",
    "_pending_message",
)


# ---------------------------------------------------------------------------
# 私有小工具
# ---------------------------------------------------------------------------


def _is_mapping(value: object) -> bool:
    """判 Mapping（不依赖顶部 import 的具体形态）。"""
    import collections.abc as collections_abc

    return isinstance(value, collections_abc.Mapping)


def _now_iso() -> str:
    return datetime_module.datetime.now(tz=datetime_module.UTC).isoformat()


def _agent_name_of(agent: agent_module.ReActAgent | None, kwargs: object) -> str:
    """取 agent 名：payload 优先，其次 agent 本体，都没有给空串。"""
    if _is_mapping(kwargs):
        name = kwargs.get("agent_name")
        if isinstance(name, str) and name.strip():
            return name
    if agent is not None:
        try:
            name = getattr(agent, "name", None)
            if isinstance(name, str) and name.strip():
                return name
        except Exception:  # noqa: BLE001 - name 可能是抛异常的 property
            pass
    return ""


def _thought_of(
    agent: agent_module.ReActAgent | None,
    thought_resolver: collections.abc.Callable[[typing.Any], str] | None,
) -> str:
    """取「模型即将要处理的东西」作为 ``thought``；取不到返回空串。

    推理前还没有真正的思考内容，这个参数对 TUI 来说只是个「开始思考了」的信号，
    所以取不到就给空串，不值得为它冒险去翻消息历史。
    """
    if thought_resolver is not None:
        try:
            value = thought_resolver(agent)
            if isinstance(value, str):
                return value
        except Exception as exc:  # noqa: BLE001 - 观测性取值，失败只降级
            logger.debug("[PRE_REASONING_HOOK] thought_resolver failed: %s", exc)
    if agent is not None:
        for attr in _THOUGHT_ATTRS:
            try:
                value = getattr(agent, attr, None)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


async def _notify_observer(observer: object, method_name: str, *args: object) -> bool:
    """调 observer 的某个事件方法，**逐级减参数**重试。

    ``on_agent_reasoning_start`` 在协议里是 ``(agent_name, thought)``，但具体实现
    可能只收 ``(agent_name)``，甚至根本没有这个方法。这里从完整位置参数开始试，
    ``TypeError`` 就砍掉最后一个参数再来，直到无参调用；全都失败就返回 ``False``。

    返回是否**确实发出去了**（方法不存在也算没发出去）。
    """
    if observer is None:
        return False
    try:
        method = getattr(observer, method_name, None)
    except Exception:  # noqa: BLE001 - 代理对象的 __getattr__ 可能抛
        return False
    if method is None or not callable(method):
        return False

    candidates: list[tuple[object, ...]] = [args]
    for end in range(len(args) - 1, -1, -1):
        candidates.append(args[:end])

    last_error: BaseException | None = None
    for call_args in candidates:
        try:
            result = method(*call_args)
            if inspect.isawaitable(result):
                await result
            return True
        except TypeError as exc:
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 - observer 出错不该打断主链路
            logger.debug(
                "[PRE_REASONING_HOOK] observer.%s raised: %s", method_name, exc
            )
            return False
    if last_error is not None:
        logger.debug(
            "[PRE_REASONING_HOOK] observer.%s signature mismatch: %s",
            method_name,
            last_error,
        )
    return False


def _emit_telemetry(
    agent_name: str,
    *,
    iteration: int | None,
    session_id: str | None,
    started_at: str,
) -> bool:
    """尽力发一条遥测；``emitter`` 没落地或没有对应函数就返回 ``False``。"""
    for name in _TELEMETRY_CANDIDATES:
        func = getattr(emitter, name, None)
        if func is None or not callable(func):
            continue
        try:
            result = func(
                agent_name=agent_name,
                iteration=iteration,
                session_id=session_id,
                started_at=started_at,
            )
            if inspect.isawaitable(result):
                # 同步上下文里不该 await（钩子可能在同步路径被调），关闭避免
                # "coroutine never awaited" 告警。
                result.close()
            return True
        except TypeError:
            # 签名不是关键字形式，退回只传 agent_name 再试一次
            try:
                result = func(agent_name)
                if inspect.isawaitable(result):
                    result.close()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("[PRE_REASONING_HOOK] telemetry %s failed: %s", name, exc)
                return False
        except Exception as exc:  # noqa: BLE001
            logger.debug("[PRE_REASONING_HOOK] telemetry %s failed: %s", name, exc)
            return False
    return False


class AgentPreReasoningHookAttacher:
    """把「推理前」这一刻挂到宿主 hook 上，并上报给 observer / 遥测。

    用法（``AgentRuntimeHookBuilder.attach_pre_reasoning_hook`` 里）::

        AgentPreReasoningHookAttacher(
            config_manager=...,
            session_id_resolver=...,
            observer_resolver=...,
        ).attach(agent)

    所有构造参数都可省略：观测性钩子不该因为缺一个 resolver 就让装配失败。

    :param config_manager: 配置源（预留，当前只做保存）。
    :param session_id_resolver: 取当前 session_id 的可调用对象。
    :param observer_resolver: 取 observer 的可调用对象；**也可以直接传 observer
        实例**（不可调用时按实例处理），``None`` 表示不上报。
    :param app_resolver: 取 app 实例的可调用对象（预留，供后续扩展）。
    :param thought_resolver: 取 ``thought`` 的可调用对象（``agent -> str``）。
    :param clock: 单调时钟，注入以便测试。
    """

    def __init__(
        self,
        *,
        session_id_resolver: collections.abc.Callable[[], str | None] ,
        message_queue_resolver: collections.abc.Callable[[], runtime_ports.MessageQueueProtocol | None] ,
        scheduler_resolver: collections.abc.Callable[[], object | None],
    ) -> None:
        self._message_queue_resolver = message_queue_resolver
        self._session_id_resolver = session_id_resolver
        self._scheduler_resolver = scheduler_resolver

    def attach(
        self,
        agent: agent_module.ReActAgent,
    ) -> None:

        async def inject_queued_messages(
            agent_self: agent_module.ReActAgent,
            kwargs: dict[str, object],
        ) -> None:
            del kwargs
            try:
                await self._run(agent_self)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(exc)
                raise
        agent.register_instance_hook(_HOOK_EVENT, "inject_queued_messages", inject_queued_messages)


    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def _run(
        self,
        agent: agent_module.ReActAgent,
    ) -> None:
        """一次 ``pre_reasoning`` 的实际动作。

        顺序：登记 agent -> 解析上下文 -> 打点 -> 上报 observer -> 遥测。
        任一步失败都只记 debug 并继续，且**不抛异常**。
        """
        session_id = self._session_id_resolver()
        if not session_id:
            return
        await self._sync_wait_for_children(agent, session_id)
        message_queue = self._message_queue_resolver()
        if message_queue is None:
            return
        reservation= message_queue.reserve(session_id, agent.name)
        if reservation is None:
            return
        try:
            await self._deliver_reservation(agent,message_queue, reservation)
        finally:
            message_queue.release_reservation_owner(reservation.reservation_id)

    async def _deliver_reservation(
        self,
        agent: agent_module.ReActAgent,
        message_queue: runtime_ports.MessageQueueProtocol,
        reservation: runtime_ports.QueuedMessageReservationProtocol,
    )-> None:
       try:
           runtime_messages = self._normalize_runtime_message(
               [self._runtime_message(agent,queued_message) for queued_message in reservation.messages]
           )
           if not reservation.context_persisted and not reservation.context_persistence_indeterminate:
               self._validate_context_messages(agent,runtime_messages, require_call=False)
       except (Exception, asyncio.CancelledError) as exc:
           if not reservation.context_persisted and not reservation.context_persistence_indeterminate:
                message_queue.restore_reservation(reservation.reservation_id)
           raise
       expected_message_ids = tuple(runtime_message.id for runtime_message in runtime_messages)
       if reservation.context_persisted:
           self._validate_context_messages(agent,runtime_messages, require_call=True)
       elif reservation.context_persistence_indeterminate:
           self._validate_context_messages(agent,runtime_messages, require_call=True)
           await self._retry_indeterminate_persistence(
               agent,
               message_queue,
               reservation,
               expected_message_ids,
           )
       else:
           await self._admit_fresh_reservation(
               agent,
               message_queue,
               reservation,
               runtime_messages,
               expected_message_ids,
           )
       if not reservation.context_committed:
           if not await  message_queue.complete_reservation_admission(reservation.reservation_id):
               raise RuntimeError("queued message reservation disappeared before adminssion completion")
           if not message_queue.commit_reservation(reservation.reservation_id):
               raise RuntimeError("queued message reservation disappeared before commit")
           self._log_injected_message(agent,reservation.messages)

    async def _admit_fresh_reservation(
        self,
        agent: agent_module.ReActAgent,
        message_queue: runtime_ports.MessageQueueProtocol,
        reservation: runtime_ports.QueuedMessageReservationProtocol,
        runtime_messages: list[message.Msg],
        expected_message_ids: tuple[str,...],
    )->None:
        try:
            prepared = await message_queue.prepare_reservation(reservation.reservation_id)
        except (Exception, asyncio.CancelledError):
            message_queue.restore_reservation(reservation.reservation_id)
            raise
        if not prepared:
            message_queue.restore_reservation(reservation.reservation_id)
            raise RuntimeError("reservation did not prepare")
        existing_ids = frozenset(runtime_message.id for runtime_message in agent.context.content)
        added_ids = tuple(message_id for message_id in expected_message_ids if message_id not in existing_ids)
        try:
            await agent.context.add(runtime_messages)
            self._validate_context_messages(agent,runtime_messages, require_call=True)
        except (Exception, asyncio.CancelledError):
            await self._rollback_context_admission(agent,added_ids)
            message_queue.restore_reservation(reservation.reservation_id)
            raise
        try:
            marked = message_queue.mark_reservation_context_persistence_indeterminate(reservation.reservation_id,added_ids)
        except (Exception, asyncio.CancelledError):
            await self._rollback_context_admission(agent,added_ids)
            if not message_queue.restore_reservation(reservation.reservation_id):
                message_queue.restore_indeterminate_reservation(reservation.reservation_id)
            raise
        if not marked:
            await self._rollback_context_admission(agent,added_ids)
            if not message_queue.restore_reservation(reservation.reservation_id):
                message_queue.restore_indeterminate_reservation(reservation.reservation_id)
            raise RuntimeError("reservation did not prepare")
        await self._persist_pinned_reservation(
            agent,
            message_queue,
            reservation,
            expected_message_ids,
            added_ids,
        )

    # ------------------------------------------------------------------
    # 推理前：子 agent 同步等待 / 队列消息归一 / 校验 / 持久化 / 回滚 / 记账
    # ------------------------------------------------------------------

    async def _sync_wait_for_children(
        self,
        agent: agent_module.ReActAgent,
        session_id: str,
    ) -> None:
            try:
               await self._perform_sync_wait(agent,session_id)
            except Exception as exc:  # noqa: BLE001 - 等待是旁路能力
                logger.error(exc)

    @staticmethod
    def _runtime_message(
        agent: agent_module.ReActAgent,
        queued_message: runtime_ports.QueuedMessageProtocol,
    ) -> message.Msg:
        return agent_queued_context.project_queued_context_message(agent.name, queued_message)

    @staticmethod
    def _normalize_runtime_message(
        runtime_messages: list[message.Msg] ,
    ) -> list[message.Msg]:
        normalized: list[message.Msg] = []
        states_by_id: dict[str, conversation_context.ContextMessageState] = {}
        for runtime_message in runtime_messages:
            state = AgentPreReasoningHookAttacher._comparable_message_state(runtime_message)
            previous= states_by_id.get(runtime_message.id)
            if previous is None:
                states_by_id[runtime_message.id] = state
                normalized.append(runtime_message)
                continue
            if previous != state:
                raise RuntimeError("queued_message batch contains ")
        return normalized
    @staticmethod
    def _validate_context_messages(
        agent: agent_module.ReActAgent,
        experted_messages: abc.Collection[message.Msg] ,
        *,
        require_call: bool,
    ) -> None:
        live_by_id:dict[str,list[conversation_context.ContextMessageState]] = {}
        for runtime_message in agent.context.context:
            live_by_id.setdefault(runtime_message.id, []).append(
                AgentPreReasoningHookAttacher._comparable_message_state(runtime_message)
            )
        for experted_message in experted_messages:
            live_states= live_by_id.get(experted_message.id,[])
            if not live_states:
                if require_call:
                    raise RuntimeError("persisted queued-message reservation")
                continue
            expected_state =AgentPreReasoningHookAttacher._comparable_message_state(experted_message)
            if len(live_states) != 1 or live_states[0] != expected_state:
                raise RuntimeError("queued messages contain duplicated message ids")

    async def _retry_indeterminate_persistence(
        self,
        agent: agent_module.ReActAgent,
        message_queue: runtime_ports.MessageQueueProtocol,
        reservation: runtime_ports.QueuedMessageReservationProtocol,
        expected_message_ids: tuple[str,...],
    ) -> None:
        await self._persist_pinned_reservation(agent, message_queue, reservation, expected_message_ids, reservation.content_added_message_ids)

    @staticmethod
    def _log_injected_message(
        agent: agent_module.ReActAgent,
        queued_messages: collections.abc.Sequence[runtime_ports.QueuedAgentMessageProtocol] ,
    ) -> None:
        for queued_message in queued_messages:
            content = queued_message.content
            source = queued_message.source
            preview = f"{content[:100]}{'...' if len(content) > 100 else ''}"
            logger.info(
                "Injected message to agent %s ：%s from source %s",agent.name, preview, source,
            )
    @staticmethod
    async def _rollback_context_admission(
        agent: agent_module.ReActAgent,
        added_message_ids: abc.Collection[str] ,
    ) -> None:
        current_ids= frozenset(runtime_message.id for runtime_message in agent.context.context)
        rollback_ids = [
            message_id
            for message_id in dict.fromkeys(added_message_ids)
            if message_id in current_ids
        ]
        if not rollback_ids:
            return
        try:
            await agent.context.delete(rollback_ids)
        except (Exception,asyncio.CancelledError)  as exc:  # noqa: BLE001
            logger.debug(
                "[PRE_REASONING_HOOK] rollback context admission failed: %s", exc
            )
            raise
        remaining_ids = frozenset(runtime_message.id for runtime_message in agent.context.context)
        if remaining_ids.intersection(rollback_ids):
            raise RuntimeError("Context rollback dit not remove every queued message")

    async def _persist_pinned_reservation(
        self,
        agent: agent_module.ReActAgent,
        message_queue: runtime_ports.MessageQueueProtocol,
        reservation: runtime_ports.QueuedMessageReservationProtocol,
        expected_message_ids: tuple[str,...],
        added_message_ids: tuple[str,...],
    ) -> None:
        try:
            await agent.persist_context_memory_strict(expected_message_ids)

        except  asyncio.CancelledError:
            raise
        except conversation_context.ContextPersistenceIndeterminateError:
            raise
        except Exception :
            await self._rollback_context_admission(agent, added_message_ids)
            message_queue.restore_indeterminate_reservation(reservation.reservation_id)
            raise
        if not message_queue.mark_reservation_context_persisted(reservation.reservation_id):
            raise  RuntimeError("persisted queued-message reservation")

    async def _perform_sync_wait(
        self,
        agent: agent_module.ReActAgent,
        session_id: str,
    ) -> None:
        context= agent.agent_context
        if context is None:
            return
        registry= context.registry or file_agent_registry.load_agent_registry(session_id)
        sync_pending= self._find_sync_pending(context, registry)
        if not sync_pending:
            return
        # _mark_parent_waiting / _wait_for_children / _restore_parent /
        # _running_scheduler 同样尚未实现（只有调用、没有定义）。逐个探测后再走，
        # 缺失的环节安静跳过：默认 wait_mode=ASYNC 时本来就到不了这里，
        # 即使有 SYNC 子 agent，也不应因为等待能力未落地而让整轮推理失败。
        mark_waiting = getattr(self, "_mark_parent_waiting", None)
        if mark_waiting is not None:
            mark_waiting(agent, context, registry, sync_pending)
        wait_for_children = getattr(self, "_wait_for_children", None)
        if wait_for_children is not None:
            scheduler_getter = getattr(self, "_running_scheduler", None)
            scheduler = scheduler_getter() if scheduler_getter is not None else None
            await wait_for_children(agent, registry, scheduler, sync_pending)
        restore_parent = getattr(self, "_restore_parent", None)
        if restore_parent is not None:
            restore_parent(agent, context, registry)

    @staticmethod
    def _find_sync_pending(
        context: agent_context_module.AgentContext,
        registry: object,
    ) -> list:
        """找出需要父 agent 同步等待的子 agent。

        原实现缺失，调用处直接抛
        ``'AgentPreReasoningHookAttacher' object has no attribute
        '_find_sync_pending'``，使每轮推理前的"子 agent 同步等待"能力失效。

        判定依据来自 ``AgentContext.wait_mode``：只有显式登记为
        ``AgentWaitMode.SYNC`` 的子 agent 才需要父 agent 等待；默认是
        ``ASYNC``，因此大多数情况下这里返回空列表，行为与"无需等待"一致。
        """
        parent_id = getattr(context, "agent_id", None)
        if not parent_id:
            return []
        getter = getattr(registry, "get_children", None)
        if getter is None:
            return []
        try:
            children = getter(parent_id)
        except Exception:  # noqa: BLE001 - registry 为旁路依赖，取不到就视为无需等待
            return []
        sync_mode = agent_context_module.AgentWaitMode.SYNC
        pending = []
        for child in children or ():
            if getattr(child, "wait_mode", None) == sync_mode:
                pending.append(child)
        return pending

    @staticmethod
    def _comparable_message_state(
        runtime_message: message.Msg,
    ) -> conversation_context.ContextMessageState:
        return agent_queued_context.comparable_context_message_state(runtime_message)



