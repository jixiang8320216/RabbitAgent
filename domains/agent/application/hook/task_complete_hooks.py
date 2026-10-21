"""任务完成（task complete）hook 的挂载器。

``AgentRuntimeHookBuilder.attach_all_hooks`` 里的第 9 步是它，注释写着「必须先于
``agent_call_end``」。这两条决定了本文件的形状：

1. **没有 ``task_complete`` 这个事件**。``ReActAgent.HOOK_TYPES`` 里只有
   ``pre_reply`` / ``post_reply`` / ``pre_reasoning`` / ... ，``register_instance_hook``
   对未注册的事件会抛 ``ValueError``。所以本类挂在 **``post_reply``** 上——它是
   「一次调用结束」的唯一事件点。
2. **靠注册顺序保证先后**。``_hooks`` 是 ``{event: OrderedDict}``，``_fire_hooks``
   按**插入顺序**遍历。``attach_all_hooks`` 里第 9 步在第 10 步之前被调用，那么本钩子
   就会排在 ``agent_call_end`` 的回调前面先执行——不需要额外的优先级机制。

三个方法的分工：

- :meth:`attach` —— 注册回调；
- :meth:`_run_task_complete` —— 归一化这一次的结果（成功/失败、正文、错误、耗时）；
- :meth:`_dispatch_task_complete` —— 把「完成了」这件事分发出去（observer → agent 树
  → 遥测）。

设计约束（与兄弟挂载器一致）：

- **一切失败只记日志**。这是观测性钩子，observer / emitter / agent 树都可能不存在。
- **绝不能消费别人的数据**。``agent.take_last_response_text()`` 会把 ``_last_response_text``
  **取走并置空**，而第 10 步 ``agent_call_end`` 还要用它做最终回复刷新。所以这里只用
  **非破坏性的 peek**，不能调 ``take_`` 系列。
- **hook 返回值会改写 payload**（``_fire_hooks`` 里 ``result is not None`` 就替换），
  所以 :meth:`_run_task_complete` 不返回内容。
"""

from __future__ import annotations

import asyncio
import collections.abc as abc
import logging
import pathlib
import typing

import domains.plugin.application.request_context as plugin_context
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.plugin.domain as plugin_contracts
import domains.agent.domain.aggregates.scope_context as scope_context
import domains.agent.domain.value_objects.message as message
logger = logging.getLogger(__name__)

__all__ = ["AgentTaskCompleteHookAttacher"]


# 见模块 docstring：task_complete 只能搭 post_reply 的车。
_HOOK_EVENT = "post_reply"
_HOOK_NAME = "send_task_complete"


def _resolve_status(exception:object) -> plugin_contracts.TaskCompleteStatus:
    """把 ``post_reply`` 的 ``exception`` 归一成「任务状态」。

    - ``None`` → ``completed``（正常跑完）；
    - ``asyncio.CancelledError`` / ``KeyboardInterrupt`` → ``interrupted``；
    - 其余异常 → ``failed``。

    返回值优先是 ``plugin_contracts.TaskStatus`` 的成员（枚举一旦落地即自动生效）；
    取不到就返回通行的状态字符串。
    """
    if isinstance(exception, asyncio.CancelledError):
        return plugin_contracts.TaskCompleteStatus.INTERRUPTED
    active_scope = scope_context.get_turn_scope_context()
    if active_scope is not None and active_scope.cancelled:
        return plugin_contracts.TaskCompleteStatus.INTERRUPTED
    if exception is not None:
        return plugin_contracts.TaskCompleteStatus.FAILED
    return plugin_contracts.TaskCompleteStatus.COMPLETED


def _resolve_project_home(raw_home: str|None) -> pathlib.Path|None:
    """把 resolver 的返回值归一成字符串形式的 project_home。

    ``_project_home_resolver()`` 声明的是 ``str | None``，但实现可能给
    ``pathlib.Path``；这里是任务完成回调的输入，统一收成字符串更省心。
    ``None`` 或转换失败时先兜地问 ``execution_context.get_project_home()``，
    再不行返回空串（``invocation_id`` 那边也是用 ``or ""`` 兜底的）。
    """
    if not raw_home:
        return None
    try:
        return pathlib.Path(raw_home)
    except (TypeError,ValueError):
        logger.debug("[TASK_COMPLETE_HOOK] normalize project_home failed: %s", raw_home)
        return None

def _message_text(value: object) -> str:
    if isinstance(value, message.Msg) :
        return value.get_text_content() or ""
    return ""


def _error_summary(status: plugin_contracts.TaskCompleteStatus) -> str :
    """按状态给出一句话错误摘要；**非错误状态返回 ``None``**。

    调用点传的是 **status**（不是 ``exception``），所以这里只能按状态给标签。
    为了容错，若传进来的其实是异常对象，也会按 ``"TypeName: message"`` 摘要。
    """
    if status is plugin_contracts.TaskCompleteStatus.INTERRUPTED:
        return "turn_cancelled"
    if status is plugin_contracts.TaskCompleteStatus.FAILED:
        return "turn_failed"
    return ""


class AgentTaskCompleteHookAttacher:
    """在一次调用收尾时上报「任务完成」。

    用法（``AgentRuntimeHookBuilder._attach_task_complete_hook`` 里）::

        AgentTaskCompleteHookAttacher(
            config_manager=...,
            session_id_resolver=...,
            observer_resolver=...,
        ).attach(agent)

    所有构造参数都可省略：观测性钩子不该因为缺一个 resolver 就让装配失败。

    :param config_manager: 配置源（预留，当前只做保存）。
    :param session_id_resolver: 取当前 session_id 的可调用对象。
    :param observer_resolver: 取 observer 的可调用对象；**也可以直接传 observer
        实例**（不可调用时按实例处理），``None`` 表示不上报。
    :param app_resolver: 取 app 实例的可调用对象（用于 ``app.agent_tree`` 记账）。
    :param answer_presenter: 是否顺便把最终回复 comment 给 observer 做定稿
        （``on_agent_text_stream`` + ``on_agent_text_stream_end``）。builder 把
        task_complete 排在 agent_call_end 之前，就是为了让它先落这一步。
    :param clock: 单调时钟，注入以便测试。
    """

    def __init__(
        self,
        *,
        session_id_resolver: abc.Callable[[], str | None] ,
        project_home_resolver: abc.Callable[[], str | None],
        parent_instance_resolver: abc.Callable[[], str | None],
    ) -> None:
        self._project_home_resolver= project_home_resolver
        self._session_id_resolver = session_id_resolver
        self._parent_instance_resolver = parent_instance_resolver

    # ------------------------------------------------------------------
    # 挂载
    # ------------------------------------------------------------------

    def attach(
        self,
        agent: agent_module.ReActAgent,
    ) -> None:
        async def plugin_task_complete_hook(
            agent_self: agent_module.ReActAgent,
            kwargs: dict[str, object],
        ) -> None:
            await self._run_task_complete(agent_self, kwargs)

        agent.register_instance_hook("post_reply", "plugin_task_complete", plugin_task_complete_hook)

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def _run_task_complete(
        self,
        agent: agent_module.ReActAgent,
        kwargs: typing.Mapping[str, object],
    ) -> None:
        """归一化这一次的结果，然后交给 :meth:`_dispatch_task_complete`。

        顺序：解析上下文 → 组装 outcome → 分发 → 记账。
        任一步失败都只记 debug 并继续，且**不抛异常**。
        """
        try:
            await self._dispatch_task_complete(agent, kwargs)
        except Exception as exc:  # noqa: BLE001 - 观测性钩子绝不能打断一轮对话
            logger.debug("[TASK_COMPLETE_HOOK] task_complete failed: %s", exc)

    async def _dispatch_task_complete(
        self,
        agent: agent_module.ReActAgent | None,
        kwargs: typing.Mapping[str, object],
    ) -> None:
        """把「完成了」分发到 observer / agent 树 / 遥测。

        三条通道彼此独立，任一失败不影响其他；返回是否**至少有一条**真的发出去了。
        """
        if self._parent_instance_resolver() is not None:
            return
        session_id = self._session_id_resolver()
        if not session_id:
            return
        manager = plugin_context.get_plugin_manager()
        if manager is None or not manager.has_active_plugin():
            return
        exception = kwargs.get("exception")
        status = _resolve_status(exception)
        task_input=plugin_contracts.TaskCompleteInput(
            session_id=session_id,
            agent_name=agent.name,
            invocation_id = execution_context.get_current_invocation_id() or "",
            project_home=_resolve_project_home(self._project_home_resolver()),
            user_message= _message_text(kwargs.get("msg")),
            final_response= _message_text(kwargs.get("response")),
            status=status,
            error= _error_summary(status),
        )
        await manager.run_task_complete_hook(task_input,plugin_contracts.TaskCompleteOutput())

