"""轮次 hook 协调器（SessionHookTurnCoordinator）。

``SessionOrchestrator`` 在每个用户轮次的三个点委托到这里：

- ``_hook_metadata`` —— 构造一次 hook 派发用的 ``HookLifecycleMetadata``。
  签名与 ``react_agent.py`` 的 ``_HookLifecycleApplicationPort.
  build_hook_lifecycle_metadata`` 逐字对齐，app 侧那两个调用点（含 orchestrator
  的委派链）靠它拿元数据；
- ``_run_prompt_hooks`` —— 用户消息进模型前跑 ``UserPromptSubmit``，返回
  ``(user_msg, blocked_response, async_delivery_ids)`` 三元组；
- ``_run_turn_completion_hooks`` —— agent 出结果后跑 ``Stop``，返回（可能被
  hook 追加了内容的）response。

三条铁律（与 ``session_turn_execution`` 的降级哲学一致）：

1. hook 是旁路能力——``app.hook_lifecycle`` 没装配、方法没上线、派发抛异常，
   一律降级成「没跑过 hook」，绝不让本轮对话失败；
2. ``asyncio.CancelledError`` 必须透传，取消不能被子系统吞掉；
3. 消息改写只做「追加文本」这一种最小动作，不重排、不删除原有内容。
"""

from __future__ import annotations

import logging
import typing


import domains.agent.domain.value_objects.message as message_module
import domains.runtime.application.hook_lifecycle as hook_lifecycle_module
import domains.runtime.application.orchestrators.session_hook_contracts as hook_contracts
import domains.hook.domain.inputs as hook_inputs
import domains.agent.domain.aggregates.react_agent as agent
import domains.configuration.domain.runtime_state.prompt_expansion as prompt_expansion_module
logger = logging.getLogger(__name__)

__all__ = ["SessionHookTurnCoordinator"]

_LOG_PREFIX = "[TURN-HOOK] "

# hook 派发结果上可能挂异步投递 id 的字段名。``HookDispatchResult`` 尚未落地
# （``hook/domain/runtime_models`` 是 0 字节骨架），真身字段名确定后这里收敛成一个。
_DELIVERY_ID_FIELDS: tuple[str, ...] = (
    "async_delivery_ids",
    "delivery_ids",
    "async_deliveries",
)

# 判定「拦截」的动作集合，与 ``react_agent._dispatch_post_tool_batch`` 的口径一致。
_BLOCKING_ACTIONS: frozenset[str] = frozenset({"block", "deny"})


class SessionHookTurnCoordinator:


    def __init__(self, app: hook_contracts.SessionHookApplication)->None:
        self.app = app

    def _hook_metadata(
        self,
        session_id: str,
        *,
        correlation_id: str,
        prompt_id: str | None = None,
        entry_agent: typing.Any = None,
        model_index: int | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
    ) -> hook_lifecycle_module.HookLifecycleMetadata:
        execution_context =self.app.execution_context
        cwd=(
            execution_context.working_directory
            if execution_context is not None and execution_context.working_directory
            else self.app.user_project_home
        )
        current_model = entry_agent.model.model_name if entry_agent is not None else None
        resolved_agent_id = (agent_id if agent_id is not None else (entry_agent.name if entry_agent is not None else None) )
        resolved_agent_type = (
            agent_type
            if agent_type is not None
            else (entry_agent.hook_agent_type if entry_agent is not None else None)
        )
        profiles,selected_profile = self._hook_model_profiles(model_index)
        return hook_lifecycle_module.HookLifecycleMetadata(
            session_id=session_id,
            correlation_id=correlation_id,
            cwd=str(cwd) if cwd is not None else None,
            permission_mode= hook_inputs.PermissionMode.DEFAULT,
            prompt_id=prompt_id,
            agent_id=resolved_agent_id,
            agent_type=resolved_agent_type,
            current_model=current_model,
            current_model_profile_id=selected_profile,
            compatible_model_profiles= profiles,
        )

    async def _run_prompt_hooks(
        self,
        entry_agent: agent.ReActAgent,
        user_msg: message_module.Msg ,
        session_id: str ,
        expansion:prompt_expansion_module.PromptExpansion|None,
        *,
        model_index: int | None ,
    ) -> tuple[message_module.Msg, message_module.Msg| None, tuple[str,...]]:
        current_message=user_msg
        expansion_hooks = getattr(self, "_run_user_prompt_expansion_hook", None)
        if expansion is not None and expansion_hooks is not None:
            try:
                current_message, blocked = await  expansion_hooks(
                    entry_agent,
                    current_message,
                    session_id,
                    expansion,
                    model_index=model_index,
                )
            except Exception:
                logger.exception("%sprompt hooks failed", _LOG_PREFIX)
            else:
                if blocked is not None:
                    return current_message, blocked,()

        user_hook = getattr(self, "_run_user_prompt_hook", None)
        if user_hook is None:
            # hook 层尚未实现 _run_user_prompt_hook：直接透传，prompt 编排
            # 仍能正常工作（仅少了 hook 旁路能力）。
            return current_message, None, []

        try:
            return await user_hook(
                entry_agent,
                current_message,
                session_id,
                model_index=model_index,
            )

        except Exception as exc:  # noqa: BLE001 - hook 是旁路能力，失败不阻断本轮
            logger.debug("%sprompt hooks degraded: %s", _LOG_PREFIX, exc)
            return current_message, None, []


    async def _run_turn_completion_hooks(
        self,
        entry_agent: agent.ReActAgent,
        response: message_module.Msg ,
        session_id: str ,
        *,
        model_index: int | None ,
    ) -> message_module.Msg:
        # Stop hook 与 Notification hook 的具体实现尚未落地：
        # _run_stop_hooks / _run_notification_hook / _run_user_prompt_hook /
        # _run_user_prompt_expansion_hook 在本类里都只有调用、没有定义，
        # 直接调用会抛 AttributeError（本类原本只有 _run_prompt_hooks 与
        # _run_turn_completion_hooks 两个方法）。
        # 这里统一按"存在才调用"降级：hook 属旁路能力，未实现时安静跳过，
        # 绝不阻断本轮对话；待 hook 层补齐后无需再改这里。
        stop_hooks = getattr(self, "_run_stop_hooks", None)
        if stop_hooks is not None:
            try:
                response = await stop_hooks(
                    entry_agent,
                    response,
                    session_id,
                    model_index=model_index,
                )
            except Exception:
                logger.exception("%sstop hooks failed", _LOG_PREFIX)

        notify_hook = getattr(self, "_run_notification_hook", None)
        if notify_hook is not None:
            try:
                await notify_hook(
                    entry_agent,
                    session_id,
                    message="Rabbit completed the response and is waiting for your next prompt",
                    notification_type=hook_inputs.NotificationType.IDLE_PROMPT,
                    title="Rabbit is ready",
                    prompt_id=response.id,
                    model_index=model_index,
                )
            except Exception as exc:  # noqa: BLE001 - hook 是旁路能力，失败不阻断本轮
                logger.debug("%sstop hooks degraded: %s", _LOG_PREFIX, exc)
        return response



