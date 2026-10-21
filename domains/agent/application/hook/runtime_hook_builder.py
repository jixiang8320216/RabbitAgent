"""Agent 运行时钩子装配（``AgentRuntimeHookBuilder``）。

``agent_runtime_composition._finalize_agent`` 在 agent 构造完之后用它把钩子挂上：

    resolved_hook_builder.attach_all_hooks(
        agent=..., is_root_agent=..., available_tools=...
    )

钩子回调的调用形状由 ``ReActAgent._fire_hooks`` 决定：``nparams < 3`` 时按
``(agent, payload)`` 调用，所以这里所有回调都写成 ``(agent, payload)`` 两参。

**钩子一律不得抛出**：一轮对话的成败不该被一个可选的观察/埋点钩子左右。所有
回调内部都吞掉异常并只记 debug 日志。
"""

from __future__ import annotations

import collections.abc as cabc
import datetime as datetime_module
import logging
import typing

import domains.observability.application.emitter as emitter
import domains.agent.application.toolkit.observability_metadata as metadata_resolver_module
import domains.agent.application.hook.pre_reasoning_hooks as pre_reasoning_hooks
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.agent.application.hook.task_complete_hooks as task_complete_hooks
import domains.agent.application.hook.runtime_hook_contracts as hook_contracts
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
import domains.agent.application.hook.tool_event_hooks as tool_event_hooks
import domains.agent.application.hook.call_lifecycle_hooks as lifecycle_hooks
import domains.agent.application.hook.runtime_hook_support as hook_support
from domains.agent.domain.aggregates import execution_context

logger = logging.getLogger(__name__)

__all__ = ["AgentRuntimeHookBuilder"]

get_agent_scheduler = execution_context.get_agent_scheduler
get_message_queue = runtime_bindings.get_message_queue
get_current_parent_agent_instance_id = execution_context.get_current_parent_agent_instance_id
get_app_instance = execution_context.get_app_instance
get_root_agent_id= execution_context.get_root_agent_id
get_interrupt_checker=runtime_bindings.get_interrupt_checker


def _now() -> str:
    return datetime_module.datetime.now(tz=datetime_module.UTC).isoformat()


class AgentRuntimeHookBuilder:
    """把 agent 生命周期事件接到 observer / 诊断上。

    :param token_counter: token 计数器（预留，当前只做透传保存）。
    :param config_manager: 配置源（预留）。
    :param session_id_func: 取当前 session_id 的可调用对象。
    :param observer_instance: ``RabbitObserver``；``None`` 表示不上报。
    """

    def __init__(
        self,
        token_counter: typing.Any | None = None,
        config_manager: typing.Any | None = None,
        session_id_func: cabc.Callable[[], str | None] | None = None,
        observer_instance: typing.Any | None = None,
    ) -> None:
        self.token_counter = token_counter
        self.config_manager = config_manager
        self.get_session_id = session_id_func
        self.observer_instance = observer_instance

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def attach_all_hooks(
        self,
        agent: typing.Any,
        *,
        is_root_agent: bool = False,
        available_tools: list[str] | None = None,
    ) -> None:
        #1. pre_reasoning
        self._attach_pre_reasoning_hook(agent)
        #2. compression(暂可跳过)
        # self._attach_compression_hook(agent)
        #3. tool_start
        tool_hook_state = self._attach_tool_start_hook(agent,is_root_agent,available_tools)
        #4-6. bind tool resolvers (emitter 模块未落地时跳过 telemetry/metadata)
        agent.bind_tool_telemetry_emitter(emitter.emit_tool_execute)
        agent.bind_tool_observability_metadata_resolver(metadata_resolver_module.CommandObservabilityMetadataResolver())
        self._bind_tool_args_summary_resolver(agent)
        # 7. tool_end
        self._attach_tool_end_hook(agent, tool_hook_state)
        # 8. agent_call_start
        self._attach_agent_call_start_hook(agent,is_root_agent)
        #9. task_complete (必须先于 agent_call_end)
        self._attach_task_complete_hook(agent)
        #10. agent_call_end
        self._attach_agent_call_end_hook(agent, is_root_agent)

    # def _attach_compression_hook(
    #     self,
    #     agent: agent_module.ReActAgent,
    # )->None:
    #     try:
    #         compression_hook.CompressionHook(
    #             token_counter=self.token_counter,
    #             config_manager=self.config_manager,
    #             compression_config = hook_support.build_compression_config(get_app_instance())
    #         ).attach(agent)
    #     except Exception as e:
    #         logger.error(e)

    def  _attach_pre_reasoning_hook(self, agent: agent_module.ReActAgent) -> None:
         pre_reasoning_hooks.AgentPreReasoningHookAttacher(
             session_id_resolver = self.get_session_id,
             message_queue_resolver = self._resolve_message_queue,
             scheduler_resolver = self._resolve_agent_scheduler,
         ).attach(agent)

    def _bind_tool_args_summary_resolver(self, agent: agent_module.ReActAgent) -> None:
        resolver = agent.tool_args_summary_resolver
        if resolver is None:
            return
        if not isinstance(resolver, runtime_ports.ToolArgsSummaryResolverProtocol):
            return
        agent.bind_tool_args_summary_resolver(resolver)

    def _attach_tool_start_hook(
        self,
        agent: agent_module.ReActAgent,
        is_root_agent: bool,
        available_tools: list[str] | None,
    )-> hook_contracts.ToolHookState:
        del is_root_agent, available_tools
        return self._tool_event_attacher().attach_start(agent)

    def _attach_tool_end_hook(
        self,
        agent: agent_module.ReActAgent,
        hook_state: hook_contracts.ToolHookState,
    )->None:
        self._tool_event_attacher().attach_end(agent, hook_state)

    def _attach_agent_call_start_hook(
        self,
        agent: agent_module.ReActAgent,
        is_root_agent: bool,
    )->None:
        self._lifecycle_attacher().attach_start(agent, is_root_agent=is_root_agent)

    def _attach_agent_call_end_hook(
        self,
        agent: agent_module.ReActAgent,
        is_root_agent: bool,
    )->None:
        self._lifecycle_attacher().attach_end(agent, is_root_agent=is_root_agent)

    def _attach_task_complete_hook(
        self,
        agent: agent_module.ReActAgent,
    )->None:
        task_complete_hooks.AgentTaskCompleteHookAttacher(
            session_id_resolver=self.get_session_id,
            project_home_resolver= self._resolve_project_home,
            parent_instance_resolver= self._resolve_parent_instance_id,
        ).attach(agent)

    @staticmethod
    def _resolve_message_queue()->runtime_bindings.runtime_ports.MessageQueueProtocol|None:
        return get_message_queue()



    @staticmethod
    def _resolve_agent_scheduler()->object|None :
        return get_agent_scheduler()

    def _resolve_project_home(self)->str|None:
        return execution_context.get_project_home()

    def _tool_event_attacher(self)-> tool_event_hooks.AgentToolEventHookAttacher:
        return tool_event_hooks.AgentToolEventHookAttacher(
            config_manager=self.config_manager,
            args_summary_factory=self._create_tool_args_summary,
        )

    def _lifecycle_attacher(self)->lifecycle_hooks.AgentCallLifecycleHookAttacher:
        return lifecycle_hooks.AgentCallLifecycleHookAttacher(
            session_id_resolver=self.get_session_id,
            observer_instance=self.observer_instance,
            app_resolver = self._resolve_app_instance,
            parent_instance_resolver = self._resolve_parent_instance_id,
            interrupt_checker_resolver= self._resolve_interrupt_checker,
            generate_response_decision= self._should_send_generate_response,
        )

    def _create_tool_args_summary(
        self,
        tool_name:str,
        tool_input:typing.Mapping[str,object],
        original_args:str,
    )-> str:
        return hook_support.create_tool_args_summary(tool_name, tool_input, original_args)

    @staticmethod
    def _resolve_app_instance()-> object | None:
        return get_app_instance()

    @staticmethod
    def _resolve_parent_instance_id()->str|None:
        return get_current_parent_agent_instance_id()
    @staticmethod
    def _resolve_interrupt_checker()->runtime_bindings.runtime_ports.InterruptCheckerProtocol|None:
        return get_interrupt_checker()

    def _should_send_generate_response(
        self,
        agent_name:str,
        is_root_invocation: bool,
    )->bool:
        return hook_support.should_send_generate_response(
            agent_name,
            app_instance=get_app_instance(),
            root_agent_id= get_root_agent_id(),
            is_root_invocation= is_root_invocation,
        )


















