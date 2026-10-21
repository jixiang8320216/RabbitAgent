"""agent 调用（invocation）追踪器。

一次「调用」= 某个 agent 被交给一段 task 跑一轮，用 ``instance_id`` 标识。
这里负责把调用的**开始 / 结束**绑到宿主与 agent 上、记录根调用账、发遥测、
打完成日志——全部是观测性动作。

调用方
------
``agent.service.agent_call_lifecycle_hooks.AgentCallLifecycleHookAttacher``
在 ``pre_reply`` / ``post_reply`` 钩子里依次调用：

.. code-block:: python

    _bind_invocation_start(agent, app_instance, instance_id, is_root_invocation=...)
    _emit_start_telemetry(agent, user_message, parent_id, is_root_agent=...)
    ...
    _bind_invocation_end(agent, app_instance, end_timestamp)
    if state.is_root_invocation:
        _record_root_invocation(app_instance, state, end_timestamp, agent_error)
    _log_agent_completion(agent, agent_error)

设计约束
--------

1. **绝不抛异常。** 观测链路断掉（没有 app 实例、emitter 还没实现、app 不认
   这些属性）只能是日志级别的降级，不能把一轮对话带崩。每个对外函数都吞掉
   内部异常。
2. **宿主形状不定。** ``app_instance`` 是动态对象，属性随时可能不存在，所以
   一律走 :func:`_set_attr` 这种带 ``try`` 的 ``setattr``；容器（``dict`` /
   ``list``）**惰性创建**——先 ``getattr`` 拿到就用，拿不到才建再 ``setattr``，
   必须写回，否则下一轮读到的还是旧的空容器。
3. **state 形状不定。** ``_record_root_invocation`` 拿到的 ``state`` 可能是
   ``AgentCallState``、也可能是别的鸭子类型，所以只用 ``getattr`` 取值并给默认值。
4. **遥测可有可无。** ``observability.application.emitter`` 目前是空文件，
   ``_emit_start_telemetry`` 因此按「候选函数名逐个试 → 失败退回 ``logger.debug``」
   的方式工作；将来 emitter 落地，函数名对上就被自动接管，不需要改这里。
"""

from __future__ import annotations

import datetime as datetime_module
import logging
import uuid
import domains.agent.domain.entities.agent_invocation_record as invocation_record

logger = logging.getLogger(__name__)



def _as_utc(timestamp: datetime_module.datetime) -> datetime_module.datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=datetime_module.UTC)
    return timestamp.astimezone(datetime_module.UTC)

class AgentInvocationTracker:

    def __init__(self):
        self._history:dict[str, invocation_record.AgentInvocationRecord] = {}


    # ------------------------------------------------------------------
    # 对外方法
    # ------------------------------------------------------------------

    def generate_instance_id(self, base_name: str) -> str:
        normalized_base_name= base_name.strip()
        if not normalized_base_name:
            raise ValueError("base name must not be empty")
        instance_id =f"{normalized_base_name}_{uuid.uuid4().hex[:8]}"
        return instance_id

    def on_instance_start(
        self,
        instance_id: str,
        agent_id: str,
            task: str,
        base_name: str ,
        parent_instance_id: str | None = None,
    ) -> None:
        """记录一次调用的开始。

        幂等：同一 ``instance_id`` 重复 start 只刷新起点，不重复计入运行集。
        ``instance_id`` 为空等异常输入静默忽略——观测性动作不应打断对话。
        """
        existing=self._history.get(instance_id)
        if existing is not None:
            return

        self._history[instance_id] = invocation_record.AgentInvocationRecord(
            instance_id=instance_id,
            base_name= base_name,
            agent_id =agent_id,
            parent_instance_id=parent_instance_id,
            task=task,
            started_at= datetime_module.datetime.now(datetime_module.UTC)
        )


    def on_instance_end(
        self,
        instance_id: str,
        end_timestamp: datetime_module.datetime ,
        error: Exception | None = None,
        cancelled:bool=False,
    ) -> None:
        record= self._history.get(instance_id)
        if record is None:
            return
        if cancelled:
            status:invocation_record.AgentInvocationTerminalStatus="interrupted"
        elif error is not None:
            status="failed"
        else:
            status="completed"
        finished= record.finish(
            status=status,
            ended_at=_as_utc(end_timestamp),
            error=str(error) if error is not None else None,
        )
        self._history[instance_id] = finished

    def get_instance(
        self, instance_id: str
    ) -> invocation_record.AgentInvocationRecord | None:
        record= self._history.get(instance_id)
        return record.model_copy(deep=True) if record is not None else None

    def get_running_instances(self) -> list[invocation_record.AgentInvocationRecord]:
        return [record.model_copy(deep=True)  for record in self._history.values() if record.status =="running"]

    def clear_completed(self) -> int:
        """移除所有「已完成」的调用记录，返回被清理的条数。

        运行中的调用不受影响；没有任何已完成记录时返回 ``0``。
        """
        terminal_ids=[
            instance_id for instance_id,record in self._history.items() if record.status != "running"
        ]
        for instance_id in terminal_ids:
            self._history.pop(instance_id,None)
        return len(terminal_ids)








