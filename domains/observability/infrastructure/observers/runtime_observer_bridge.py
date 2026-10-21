"""runtime 侧 observer 与 observability 收集链之间的桥。

``llm.provider`` / ``runtime.service`` 这些基础设施组件只认
``runtime.service.runtime_observer.RuntimeObserver`` 协议（异步回调），而
observability 那侧的 sink 还没接进来（``observability/application/emitter.py``
目前是空的）。为了让 provider 装配不再因为 observer 缺失而崩，这里给出
基础设施侧的默认实现：

- 所有回调都是异步 no-op（继承 ``NoOPRuntimeObserver``）；
- 可选持一个 ``delegate``，把事件转发给真正的收集器；delegate 的方法可以是
  同步也可以是异步，没有对应方法就静默跳过。

这样 ``AnthropicProvider.create_model`` 里的
``observer=runtime_observer_bridge.InfraRuntimeObserver()`` 能正常构造，等
observability sink 落地后只要传入 delegate 即可生效。
"""

from __future__ import annotations

import contextvars
import json
import typing

import datetime as datetime_module
import uuid
import logging

import domains.observability.application.observability_context as observability_context
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
import domains.observability.application.guard as guard

__all__ = ["InfraRuntimeObserver"]

import domains.hook.domain.hook_definitions as hook_models
import domains.runtime.application.hook_lifecycle as hook_lifecycle_module
import domains.agent.domain.value_objects.provider_retry_status as provider_retry_status
import domains.agent.domain.aggregates.scope_context as scope_context
from domains.agent.domain.aggregates import execution_context
from domains.observability.domain import rabbit_observer

if typing.TYPE_CHECKING:
    from domains.agent.domain.react_agent import ReActAgent as react_agent

logger = logging.getLogger(__name__)

@typing.runtime_checkable
class _ObserverApplicationPort(typing.Protocol):

    @property
    def observer(self)-> rabbit_observer.RabbbitObserver:...


class _MessageDisplayState(typing.NamedTuple):
    session_id: str
    agent_name: str
    agent_id: str | None
    turn_id:str
    message_id: str
    index:int
    pending:str



@typing.runtime_checkable
class _MessageDisplayApplicationPort(_ObserverApplicationPort, typing.Protocol):
    @property
    def hook_lifecycle(self)->hook_lifecycle_module.HookLifecycleCoordinator|None:...

    @property
    def root_agent(self)-> react_agent.ReActAgent|None:...

    def build_hook_lifecycle_metadata(
        self,
        session_id:str,
        *,
        correlation_id:str,
        prompt_id:str|None = None,
        entry_agent:react_agent.ReActAgent|None = None,
        model_index: int | None = None,
        agent_id: str | None = None,
        agent_type:str | None = None,
    )-> hook_lifecycle_module.HookLifecycleMetadata:...




class InfraRuntimeObserver:
    """基础设施侧 observer（默认无 delegate = 纯 no-op）。"""

    def __init__(self) -> None:
        self._message_display_state: contextvars.ContextVar[_MessageDisplayState | None] = contextvars.ContextVar(
            f"message_display_state_{id(self)}",
            default=None,
        )

    async def on_thinking_start(
        self,
        agent_name: str,
        operation_id: str,
        initial_prompt: str | None = None,
        tools: list[str] | None = None,
        instance_id: str | None = None,
        *,
        operation_kind: str | None = None,
    ) -> None:
        with guard.telemetry("on_thinking_start"):
            execution_context.set_current_thinking_operation_id(operation_id)
            session_id =execution_context.get_session_id()
            if not session_id or not agent_name:
                return
            app_instance = execution_context.get_app_instance()
            if not isinstance(app_instance, _ObserverApplicationPort):
                return
            cached_input = execution_context.get_cached_thinking_input()
            initial_prompt_json= initial_prompt
            if initial_prompt_json is None and cached_input:
                with guard.telemetry("on_thinking_start:cached_input"):
                    initial_prompt_json = json.dumps(cached_input, ensure_ascii=False)

        await app_instance.observer.on_thinking_operation_start(
            agent_name,
            operation_id=operation_id,
            initial_prompt=initial_prompt_json,
            available_tools= tools,
            instance_id=instance_id,
            operation_kind=operation_kind,
        )

    async def on_thinking_end(
        self,
        agent_name: str,
        operation_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        tool_call_ids: list[str] | None = None,
        reasoning_content: str | None = None,
        cached_tokens: int = 0,
        instance_id: str | None = None,
        model_id: str | None = None,
        *,
        operation_kind: str | None = None,
    ) -> None:
        with guard.telemetry("on_thinking_end"):
            session_id =execution_context.get_session_id()
            if not session_id:
                return
            app_instance = execution_context.get_app_instance()
            if not isinstance(app_instance, _ObserverApplicationPort):
                return
            cur_op = execution_context.get_current_thinking_operation_id() or operation_id or agent_name
            ids=tool_call_ids or []
        await app_instance.observer.on_thinking_operation_end(
            agent_name,
            operation_id=cur_op,
            final_content=None,
            end_timestamp= datetime_module.datetime.now().isoformat(),
            input_tokens= input_tokens,
            output_tokens= output_tokens,
            reasoning_tokens= reasoning_tokens,
            tool_call_count= len(ids),
            has_tool_calls=len(ids)>0,
            reasoning_content= reasoning_content,
            cached_tokens= cached_tokens,
            instance_id= instance_id or execution_context.get_current_invocation_id(),
            mode_id=model_id,
            operation_kind= operation_kind,
        )

    async def on_thinking_content(
        self,
        agent_name: str,
        operation_id: str | None = None,
        content: str = "",
        content_kind: str | None = None,
    ) -> None:
        with guard.telemetry("on_thinking_content"):
            ai = execution_context.get_app_instance()
            if isinstance(ai, _ObserverApplicationPort):
                cur_op = execution_context.get_current_thinking_operation_id() or operation_id or agent_name
                await ai.observer.on_thinking_content_update(
                      agent_name,
                      operation_id=cur_op,
                      content= content,
                      content_kind= content_kind,
                      instance_id=execution_context.get_current_invocation_id(),
                )
    async def on_provider_retry_status(
        self,
        status: provider_retry_status.ProviderRetryStatus,
    )->None:
        with guard.telemetry("on_provider_retry_status"):
            ai = execution_context.get_app_instance()
            if isinstance(ai, _ObserverApplicationPort):
                await ai.observer.on_provider_retry_status(status)

    async def on_text_delta(
        self,
        agent_name: str,
        delta: str,
        operation_id: str | None = None,
    ) -> None:
        with guard.telemetry("on_text_delta"):
            if not delta:
                return
            ai = execution_context.get_app_instance()
            if not isinstance(ai, _ObserverApplicationPort):
                return
            state = self._message_display_state.get()
            if not isinstance(ai, _MessageDisplayApplicationPort):
                combined=f"{state.pending}{delta}" if state is not None else delta
                self._message_display_state.set(None)
                await self._forward_text(ai,agent_name,combined,operation_id)
                return
            if state is None and not self._has_message_display_handlers(ai):
                await self._forward_text(ai,agent_name,delta,operation_id)
                return
            if state is None:
                session_id = execution_context.get_session_id()
                if not session_id:
                    await self._forward_text(ai,agent_name,delta,operation_id)
                    return
                state = _MessageDisplayState(
                    session_id=session_id,
                    agent_name=agent_name,
                    agent_id=execution_context.get_current_invocation_id(),
                    turn_id=observability_context.get_hook_turn_id() or str(uuid.uuid4()),
                    message_id=str(uuid.uuid4()),
                    index=0,
                    pending="",
                )
                combined=f"{state.pending}{delta}"
                boundary= combined.rfind("\n")
                if boundary <0:
                    self._message_display_state.set(state._replace(pending=combined))
                batch = combined[:boundary+1]
                next_state=state._replace(index=state.index+1,pending=combined[boundary+1:])
                self._message_display_state.set(next_state)
                await self._emit_message_display_batch(
                    ai,
                    state,
                    batch,
                    final=False,
                    operation_id=operation_id,
                )

    async def on_text_stream_end(
        self,
        agent_name: str,
        operation_id: str | None = None,
    ) -> None:
        with guard.telemetry("on_text_stream_end"):
            state = self._message_display_state.get()
            self._message_display_state.set(None)
            ai = execution_context.get_app_instance()
            if isinstance(ai, _ObserverApplicationPort):
                try:
                    if state is not None:
                        if isinstance(ai,_MessageDisplayApplicationPort):
                            await self._emit_message_display_batch(
                                ai,
                                state,
                                state.pending,
                                final=True,
                                operation_id=operation_id,
                            )
                        else:
                            await self._forward_text(
                                ai,
                                state.agent_name,
                                state.pending,
                                operation_id,
                                instance_id = state.agent_id,
                            )
                finally:
                    await  ai.observer.on_agent_text_stream_end(agent_name)

    async def on_tool_call_streaming(
        self,
        agent_name: str,
        tool_name: str,
        tool_id: str,
        operation_id: typing.Optional[str] = None,
        input_preview:str="",
        input_stream_phase: typing.Optional[typing.Literal["identity","arguments"]]=None,
        input_fragment:typing.Optional[str]=None,
        input_fragment_index:typing.Optional[int]=None,
        input_fragment_kind: typing.Optional[str]=None,
    ) -> None:
       with guard.telemetry("on_tool_call_streaming"):
           ai=execution_context.get_app_instance()
           if isinstance(ai, _ObserverApplicationPort):
               cur_op= execution_context.get_current_thinking_operation_id() or operation_id
               await ai.observer.on_tool_call_streaming(
                   agent_name,
                   tool_name= tool_name,
                   tool_id=tool_id,
                   operation_id=cur_op or agent_name,
                   input_preview=input_preview,
                   input_stream_phase=input_stream_phase,
                   input_fragment=input_fragment,
                   input_fragment_index=input_fragment_index,
                   input_fragment_kind=input_fragment_kind,
               )

    async def on_tool_output_streaming(
        self,
        agent_name: str,
        tool_name: str,
        tool_id: str,
        delta:str,
        stream_kind:str,
        operation_id: typing.Optional[str] = None,
        instance_id:typing.Optional[str]=None,
    ) -> None:
       with guard.telemetry("on_tool_output_streaming"):
           ai=execution_context.get_app_instance()
           if isinstance(ai, _ObserverApplicationPort):
               cur_op= execution_context.get_current_thinking_operation_id() or operation_id
               await ai.observer.on_tool_output_streaming(
                   agent_name,
                   tool_name,
                   tool_id,
                   delta,
                   stream_kind,
                   cur_op or agent_name,
                   instance_id,
               )

    def is_interrupted(self) -> bool:
        with guard.telemetry("is_interrupted"):
            execution_scope = scope_context.get_turn_scope_context()
            if execution_scope is not None:
                return execution_scope.cancelled
            session_id = execution_context.get_session_id()
            if not session_id:
                return False
            checker = runtime_bindings.get_interrupted_checker()
            if checker is not None:
                return checker.check_interrupted(session_id)
            return False
        return False

    async def on_llm_complete(
        self,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        duration_ms: int = 0,
        error: str | None = None,
        error_obj: BaseException | None = None,
        cached_tokens: int = 0,
        is_proxy: bool = False,
        is_streaming: bool = False,
    ) -> None:...

    @staticmethod
    async def _forward_text(
        ai: _ObserverApplicationPort,
        agent_name: str,
        text: str,
        operation_id: str|None = None,
        *,
        instance_id: str|None = None,
    )->None:
        if not text:
            return
        cur_op = execution_context.get_current_thinking_operation_id() or operation_id
        await ai.observer.on_agent_text_stream(
            agent_name,
            text=text,
            timestamp=datetime_module.datetime.now().isoformat(),
            operation_id=cur_op,
            instance_id=instance_id or execution_context.get_current_invocation_id(),
        )

    @staticmethod
    def _has_message_display_handlers(ai: _MessageDisplayApplicationPort)->bool:
        try:
            lifecycle= ai.hook_lifecycle
            return bool(lifecycle is not None and lifecycle.has_enabled_handlers(hook_models.HookEvent.MESSAGE_DISPLAY))
        except Exception as e:
            logger.error(e)
            return False

    async def _emit_message_display_batch(
        self,
        ai: _MessageDisplayApplicationPort,
        state: _MessageDisplayState,
        delta:str,
        *,
        final:bool,
        operation_id: str|None,
    )->None:
        display_text=delta
        lifecycle= ai.hook_lifecycle
        if lifecycle is not None:
            try:
                metadata= ai.build_hook_lifecycle_metadata(
                    state.session_id,
                    correlation_id=state.turn_id,
                    entry_agent=ai.root_agent,
                    agent_id=state.agent_id,
                    agent_type=state.agent_name,
                )
                result = await lifecycle.message_display(
                    metadata=metadata,
                    turn_id=state.turn_id,
                    message_id=state.message_id,
                    index=state.index,
                    final=final,
                    delta=delta,
                )
                if result.decision.display_content is not None:
                    display_text = result.decision.display_content
            except Exception as e:
                logger.error(e)
        await self._forward_text(
            ai,
            state.agent_name,
            display_text,
            operation_id,
            instance_id=state.agent_id,)