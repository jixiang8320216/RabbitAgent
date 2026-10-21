"""Agent 消息队列。

在同一个进程内，主 Agent 与各子 Agent 之间需要交换「系统级通知」——例如任务
完成、需要唤醒、被中断等。这些消息**不经过 LLM 上下文**，只用于调度决策，
因此需要一个独立的进程级队列。

为什么是单例（``get_instance()``）而不是每次构造新实例：

- ``runtime_bindings.set_message_queue_getter(AgentMessageQueue.get_instance)``
  把「取队列」这一能力注册成全局回调，供 runtime 层在无法直接持有应用对象的
  场景（如 hook、tool）中调用；
- 恢复服务、ReAct 循环、hook 桥接三方必须看到**同一个**队列，否则唤醒信号会
  丢失在各自的副本里。

队列为线程安全 + 可跨事件循环使用：内部使用 ``collections.deque`` 而非
``asyncio.Queue``，因为前者不绑定事件循环，能被「同步注册、异步消费」两侧
共同访问。异步等待通过内部的 ``asyncio.Event`` 实现，且采用「尽力唤醒」策略：
没有运行中的事件循环时只入队不阻塞。
"""

from __future__ import annotations

import uuid
import collections
import logging
import threading
import typing

import datetime as datetime_module


import domains.agent.domain.entities.agent_queued_message as agent_queued_message

logger = logging.getLogger(__name__)

__all__ = ["AgentMessage", "AgentMessageQueue"]


QueuedCallback=typing.Callable[[str,str, agent_queued_message.AgentQueuedMessage],None]
ConsumedCallback=typing.Callable[[str,str, list[agent_queued_message.AgentQueuedMessage]],None]
SnapshotCallback = typing.Callable[[tuple[agent_queued_message.AgentQueuedMessage,...]],None]
EnqueuedCallback = typing.Callable[[agent_queued_message.AgentQueuedMessage],None]
ClearCallback = typing.Callable[[],None]

ReservationAdmissionCallback= typing.Callable[
    [agent_queued_message.AgentMessageReservation],
    collections.abc.Awaitable[None],
]

ReservationContextCommittedCallback= typing.Callable[
    [agent_queued_message.AgentMessageReservation],
    collections.abc.Awaitable[None],
]
class AgentMessage(typing.NamedTuple):
    """一条 Agent 间消息。

    :param kind: 消息类别，例如 ``"wakeup"`` / ``"task_completed"`` / ``"interrupt"``。
    :param agent_id: 目标 Agent 标识；空串表示广播。
    :param payload: 附加数据，保持开放结构。
    """

    kind: str
    agent_id: str = ""
    payload: typing.Mapping[str, typing.Any] = {}


class AgentMessageQueue:
    """进程级 Agent 消息队列（单例）。"""

    _instance: "AgentMessageQueue | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._queues: collections.defaultdict[str, collections.defaultdict[str,list[agent_queued_message.AgentQueuedMessage]]] = collections.defaultdict(lambda: collections.defaultdict(list))
        self._active_agents: collections.defaultdict[str, set[str]] = collections.defaultdict(set)
        self._on_queued_callbacks:list[QueuedCallback]=[]
        self._on_consumed_callbacks: list[ConsumedCallback]=[]
        self._on_reservation_admission_callbacks: list[ReservationAdmissionCallback]=[]
        self._on_reservation_context_committed_callbacks:list[ReservationContextCommittedCallback]=[]
        self._reservations:dict[str, agent_queued_message.AgentMessageReservation]={}
        self._active_reservation_owners:set[str]=set()
        self._active_context_completions:set[str]=set()
        self._active_consumptions:set[str]=set()
        self._retiring_sessions:set[str]=set()
        self._retired_sessions:collections.OrderedDict[str,None] = collections.OrderedDict()


    @classmethod
    def get_instance(cls) -> "AgentMessageQueue":
        """返回进程内唯一的队列实例。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """丢弃单例（仅测试使用）。"""
        with cls._instance_lock:
            cls._instance = None

    def register_agent(self, session_id:str,agent_id:str)->None:
        self._require_identifier(session_id,"session_id")
        self._require_identifier(agent_id,"agent_id")
        with self._state_lock:
            if session_id in self._retiring_sessions or session_id in self._retired_sessions:
                return
            self._active_agents[session_id].add(agent_id)

    def reopen_session(self, session_id:str)->bool:
        self._require_identifier(session_id,"session_id")
        with self._state_lock:
            if session_id in self._retiring_sessions:
                return False
            self._retired_sessions.pop(session_id,None)
            return True


    def reserve(
        self,
        session_id: str,
        agent_id: str,
    ) -> agent_queued_message.AgentMessageReservation | None:
        with self._state_lock:
            if session_id in self._retiring_sessions or session_id in self._retired_sessions:
                return None
            scope_reservation_ids={
                reservation_id
                for reservation_id, reservation in self._reservations.items()
                if reservation.session_id ==session_id and reservation.agent_id ==agent_id
            }
            if scope_reservation_ids.intersection(
                    self._active_reservation_owners
                |self._active_context_completions
                |self._active_consumptions
            ):
                return None
            retryable_entry=next(
                (
                    (reservation_id, reservation)
                    for reservation_id, reservation in self._reservations.items()
                    if reservation.session_id == session_id
                       and reservation.agent_id == agent_id
                       and reservation_id not in self._active_reservation_owners
                ),
                None,
            )
            if retryable_entry is not None:
                reservation_id, retryable= retryable_entry
                self._active_reservation_owners.add(reservation_id)
                return retryable
            session_queues = self._queues.get(session_id)
            if session_queues is None:
                return None
            messages= session_queues.pop(agent_id,[])
            if not messages:
                return None
            if not session_queues:
                self._queues.pop(session_id, None)
            reservation =agent_queued_message.AgentMessageReservation(
                reservation_id=uuid.uuid4().hex,
                session_id=session_id,
                agent_id=agent_id,
                messages=tuple(messages),

            )
            self._reservations[reservation.reservation_id] = reservation
            self._active_reservation_owners.add(reservation.reservation_id)
            return reservation

    def enqueue(
        self,
        session_id: str,
        agent_id: str,
        content: str,
        source: str = "unknown",
        *,
        replace_source:bool=False,
        metadata:agent_queued_message.AgentMessageMetadata|None=None,
        delivery_id:str| None=None,
        timestamp: datetime_module.datetime | None=None,
        enqueued_callback: EnqueuedCallback|None=None,
    ) -> bool:
        if not content.strip():
            return False
        message= agent_queued_message.AgentQueuedMessage(
            session_id=session_id,
            agent_id=agent_id,
            content=content,
            source=source,
            timestamp=timestamp or datetime_module.datetime.now(tz=datetime_module.UTC),
            delivery_id = delivery_id or uuid.uuid4().hex,
            metadata= metadata or agent_queued_message.AgentMessageMetadata()
        )

        with self._state_lock:
            if session_id in self._retiring_sessions or session_id in self._retired_sessions:
                logger.debug(
                    "[AGENT_QUEUE] drop enqueue for retired session %s", session_id
                )
                return False
            if enqueued_callback is not None:
                try:
                    enqueued_callback(message)
                except Exception as e:
                    logger.error(e)
                    return False
            messages = self._queues[session_id][agent_id]
            if replace_source:
                messages[:]= [queued for queued in messages if queued.source != source]
            messages.append(message)
            queue_size = len(messages)
            callbacks =tuple(self._on_queued_callbacks)
        self._fire_queued_callbacks(callbacks, session_id, agent_id, message)
        return True

    def consume(
        self,
        session_id: str,
        agent_id: str,
    ) -> list[agent_queued_message.AgentQueuedMessage]:
        with self._state_lock:
            session_queues = self._queues.get(session_id)
            if session_queues is None:
                return []
            queued = session_queues.pop(agent_id, [])
            messages =[message for message in queued if message.metadata.admission_fence is None]
            retained = [message for message in queued if message.metadata.admission_fence is not None]
            if retained:
                session_queues[agent_id] = retained
            else:
                session_queues.pop(agent_id, None)
            if not session_queues:
                self._queues.pop(session_id, None)
            callbacks =tuple(self._on_queued_callbacks)
        if messages:
             self._fire_consumed_callbacks(callbacks,session_id, agent_id, messages)
        return messages

    def consume_sources(
        self,
        session_id: str,
        agent_id: str,
        source: collections.abc.Collection[str],
    ) -> list[agent_queued_message.AgentQueuedMessage]:
        """只取走 ``source`` 命中给定集合的那些消息，其余**留在队列里**。

        ``source`` 为空集合时返回空元组——「谁都不匹配」不应该被解释成「全部匹配」，
        那会把不相关的消息一起吃掉。匹配按去空白后的字符串全等比较，大小写敏感
        （与 :meth:`enqueue` 存的形态一致）。

        剩下的消息保持原有相对顺序。
        """
        selected_sources=frozenset(source)
        if not selected_sources:
            return []

        with self._state_lock:
            session_queues = self._queues.get(session_id)
            if not session_queues:
                return []
            messages = session_queues.get(agent_id)
            if messages is None:
                return []
            consumed=[
                message
                for message in messages
                if message.source in selected_sources
                   and message.metadata.admission_fence is None
            ]
            if not consumed:
                return []
            remaining=[
                message
                for message in messages
                if message.source not in selected_sources
                or message.metadata.admission_fence is not None
            ]
            if remaining:
                session_queues[agent_id] = remaining
            else:
                session_queues.pop(agent_id, None)
            if not session_queues:
                self._queues.pop(session_id, None)
            callbacks =tuple(self._on_queued_callbacks)
        self._fire_consumed_callbacks(callbacks,session_id, agent_id, messages)
        return consumed



    # ------------------------------------------------------------------
    # 预约生命周期：prepare -> (indeterminate) -> persisted -> admission -> commit
    #
    # 一次投递的完整状态机（``reserve`` 已把预约放进 ``_active_reservation_owners``）：
    #
    #   owners              预约已开出，持有方还没开始写上下文
    #     -> prepare_reservation                  -> consumptions
    #     -> mark_..._persistence_indeterminate   -> （留在 consumptions）
    #     -> mark_..._persisted                   -> context_completions
    #     -> complete_reservation_admission       -> context_completions
    #     -> commit_reservation                   -> 从 _reservations 摘除，消息正式消失
    #
    # 失败侧两条退路：
    #   * ``restore_reservation``——消息**还没**进上下文，原样还回待投递队列；
    #   * ``restore_indeterminate_reservation``——消息**已经在**上下文里（落盘未定），
    #     保留预约并置 indeterminate，下一轮由 ``reserve`` 作为可重试项重新取出，
    #     走「重做落盘」而不是「重新注入」。
    # ------------------------------------------------------------------

    async def prepare_reservation(
        self,
        reservation_id: str,
    ) -> bool:
        """标记预约已「准备好写进上下文」，并把持有权推进到消费阶段。

        调用点：``agent_pre_reasoning_hooks`` 的 ``_admit_fresh_reservation``，在
        ``context.add`` **之前**。它的意义是：先把这批消息从「有人持有」推进到
        「正在被消费」，这样并发的 ``reserve`` 不会再把同一批消息抓一遍。

        返回 ``False``：预约不存在，或持有方已被 :meth:`release_reservation_owner`
        释放（说明持有方放弃了，不该再推进状态）。

        之所以是 ``async``：这里要通知 ``_on_consumed_callbacks``，而回调可以是
        协程（比如要写一条事件日志、要等人确认）。同步的 :meth:`consume` 做不到
        这件事，只能把协程 ``close()`` 掉。
        """
        with self._state_lock:
            reservation = self._reservations.get(reservation_id)
            callbacks = list(self._on_reservation_admission_callbacks)
        if reservation is None:
            logger.debug(
                    "[AGENT_QUEUE] prepare_reservation: unknown reservation %s", reservation_id
            )
            return False
        if self._has_admission_fence(reservation) and not callbacks:
            return False
        if all(message.metadata.reservation_admission_completed for message in reservation.messages):
            return True
        for callback in callbacks:
            await callback(reservation)
        with self._state_lock:
            current=self._reservations.get(reservation_id)
            if current is None:
                return False
            self._reservations[reservation_id] = agent_queued_message.AgentMessageReservation(
                reservation_id=current.reservation_id,
                session_id=current.session_id,
                agent_id = current.agent_id,
                messages= tuple(message.with_completed_reservation_admission()for message in current.messages),
                context_added_message_ids= current.context_added_message_ids,
                context_persistence_indeterminate= current.context_persistence_indeterminate,
                context_persisted= current.context_persisted,
                context_committed= current.context_committed,
            )
            return True

    async def complete_reservation_admission(
        self,
        reservation_id: str,
    ) -> bool:
        with self._state_lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                logger.debug(
                    "[AGENT_QUEUE] complete_admission: unknown reservation %s", reservation_id
                )
                return False
            if reservation.context_committed:
                return True
            if not reservation.context_persisted or reservation.context_persistence_indeterminate:
                return False
            if reservation_id in self._active_context_completions:
                return False
            callbacks = tuple(self._on_reservation_context_committed_callbacks)
            if self._has_admission_fence(reservation) and not callbacks:
                return False
            self._active_context_completions.add(reservation_id)
            try:
                for callback in callbacks:
                    await callback(reservation)
            finally:
                with self._state_lock:
                    self._active_context_completions.discard(reservation_id)
            with self._state_lock:
                current=self._reservations.get(reservation_id)
                if current is None:
                    return False
                self._reservations[reservation_id]= current.with_context_committed()
                return True

    def mark_reservation_context_persistence_indeterminate(
        self,
        reservation_id: str,
        added_message_ids: tuple[str, ...],
    ) -> bool:
        """标记「消息已写进上下文，但这次写盘**结果未知**」。

        调用点：``_admit_fresh_reservation``，在 ``context.add`` 成功之后、落盘之前。
        记下的 ``added_message_ids``（真正新写进上下文的那些 id）是回滚时精确摘除的
        依据——不能拿整批 id，因为其中可能有一部分本来就在上下文里。

        这一步是崩溃安全的关键：进程在这一刻崩掉，重启后预约停在 indeterminate，
        下一轮会走「重做落盘」而不是「重新注入」，不会重复也**不会丢**。
        """
        with self._state_lock:
            current = self._reservations.get(reservation_id)
            if current is None:
                return False
            if current.context_persisted:
                return True
            if current.context_persistence_indeterminate:
                return current.context_added_message_ids == added_message_ids
            self._reservations[reservation_id]= current.with_context_persistence_indeterminate(
                added_message_ids
            )
            return True

    def mark_reservation_context_persisted(
        self,
        reservation_id: str,
    ) -> bool:

        with self._state_lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                return False
            if  reservation.context_persisted:
                return False
            if  not reservation.context_persistence_indeterminate:
                return False
            self._reservations[reservation_id]= reservation.with_context_persisted()
            return True

    def restore_indeterminate_reservation(
        self,
        reservation_id: str,
    ) -> bool:
        with self._state_lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None or not reservation.context_persistence_indeterminate:
                return False
            self._reservations.pop(reservation_id)
            self._active_reservation_owners.discard(reservation_id)
            queued= self._queues[reservation.session_id][reservation.agent_id]
            queued[:0]=reservation.messages
            return True

    def release_reservation_owner(
        self,
        reservation_id: str,
    ) -> bool:
        with self._state_lock:
            reservation = self._reservations.get(reservation_id)
            if reservation not in self._active_reservation_owners:
                return False
            self._active_reservation_owners.remove(reservation_id)
            return True

    def commit_reservation(
        self,
        reservation_id: str,
    ) -> bool:
        """确认投递完成：消息正式从队列里消失。

        前置条件：预约存在且**已经落盘**（``content_persisted``）。只 prepare 没
        persist 就提交，等于确认了一次「消息可能丢」的投递——崩溃后这批消息既不在
        队列里、也不在持久层里，所以这里拒绝并记录 error。

        动作：置 ``committed`` -> 从 ``_reservations`` 摘除 -> 清掉所有阶段占用
        -> **在锁外**通知 ``_on_reservation_context_committed_callbacks``。
        """
        with self._state_lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                logger.debug(
                    "[AGENT_QUEUE] commit_reservation: unknown reservation %s", reservation_id
                )
                return False
            if self._has_admission_fence(reservation) and not reservation.context_committed:
                return False
            if (
                reservation.context_persistence_indeterminate
                or reservation.context_persisted
            ) and not reservation.context_committed:
                return False
            if reservation_id in self._active_consumptions:
                return False
            self._active_consumptions.add(reservation_id)
            callbacks = tuple(self._on_consumed_callbacks)
        messages= list(reservation.messages)
        committed=False
        try:
            self._fire_consumed_callbacks(
                callbacks,
                reservation.session_id,
                reservation.agent_id,
                messages,
            )
        finally:
            with self._state_lock:
                current=self._reservations.get(reservation_id)
                self._active_reservation_owners.discard(reservation_id)
                self._active_context_completions.discard(reservation_id)
                self._active_consumptions.discard(reservation_id)
                committed= current is not None
        return committed

    def restore_reservation(
        self,
        reservation_id: str,
    ) -> bool:
        with self._state_lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                logger.debug(
                    "[AGENT_QUEUE] restore_reservation: unknown reservation %s", reservation_id
                )
                return False
            if reservation.context_persisted or reservation.context_persistence_indeterminate:
                return False
            self._reservations.pop(reservation_id)
            self._active_reservation_owners.discard(reservation_id)
            queued= self._queues[reservation.session_id][reservation.agent_id]
            queued[:0]=reservation.messages
            return True


    @staticmethod
    def _require_identifier(value:str, name:str)->None:
        if not value.strip():
            raise ValueError(f"{name} must not be empty")

    @staticmethod
    def _has_admission_fence(reservation:agent_queued_message.AgentMessageReservation,) -> bool:
        return any(message.metadata.admission_fence is not None for message in reservation.messages)


    @staticmethod
    def _fire_queued_callbacks(
        callbacks:tuple[QueuedCallback,...],
        session_id:str,
        agent_id:str,
        message: agent_queued_message.AgentQueuedMessage,
    )->None:
        for callback in callbacks:
            try:
                callback(session_id, agent_id, message)
            except Exception as exc:
                logger.error(exc)

    @staticmethod
    def _fire_consumed_callbacks(
        callbacks:tuple[ConsumedCallback,...],
        session_id:str,
        agent_id:str,
        messages: list[agent_queued_message.AgentQueuedMessage],
    )->None:
        for callback in callbacks:
            try:
                callback(session_id, agent_id, messages)
            except Exception as exc:
                logger.error(exc)






















