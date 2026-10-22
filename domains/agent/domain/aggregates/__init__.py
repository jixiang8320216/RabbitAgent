"""domains.agent.domain.aggregates 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "Agent": "domains.agent.domain.aggregates.agent_base",
    "AgentConfig": "domains.agent.domain.aggregates.agent_base",
    "AgentContextPersistenceProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "AgentResultStorePort": "domains.agent.domain.aggregates.react_agent_collaboration",
    "AgentSchedulerResolverProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "AgentTaskFactory": "domains.agent.domain.aggregates.runtime_ports",
    "AgentTaskSchedulerProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "ChatModel": "domains.agent.domain.aggregates.chat_model",
    "DEFAULT_MAX_REMINDERS": "domains.agent.domain.aggregates.tool_loop_detection",
    "DEFAULT_PREVIEW_CHARS": "domains.agent.domain.aggregates.tool_loop_detection",
    "DEFAULT_THRESHOLD": "domains.agent.domain.aggregates.tool_loop_detection",
    "DEFAULT_WINDOW_SIZE": "domains.agent.domain.aggregates.tool_loop_detection",
    "HookCallback": "domains.agent.domain.aggregates.react_agent",
    "HookContext": "domains.agent.domain.aggregates.react_agent",
    "HookResult": "domains.agent.domain.aggregates.react_agent",
    "InterruptCheckerProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "JsonObject": "domains.agent.domain.aggregates.chat_model",
    "LLM": "domains.agent.domain.aggregates.agent_base",
    "LifecycleNotifierProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "LifecycleOutcomeStatusProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "LifecycleStatusNotifierProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "MIN_PREVIEW_CHARS": "domains.agent.domain.aggregates.tool_loop_detection",
    "Memory": "domains.memory.types.reflection_memory",
    "Message": "domains.agent.domain.aggregates.agent_base",
    "MessageFormatter": "domains.agent.domain.aggregates.message_formatter",
    "MessageQueueFactoryProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "MessageQueueProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "ModelTurn": "domains.agent.domain.aggregates.react_agent_streaming",
    "NoOPRuntimeObserver": "domains.agent.domain.aggregates.runtime_observer",
    "NoOpObserver": "domains.agent.domain.aggregates.runtime_observer",
    "PlanningRuntimeProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "QueuedAgentMessageProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "QueuedMessageAdmissionFenceProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "QueuedMessageMetadataProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "QueuedMessageProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "QueuedMessageReservationProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "ReActAgent": "domains.agent.domain.aggregates.react_agent",
    "ReactAgentRuntime": "domains.agent.domain.aggregates.react_agent_collaboration",
    "ReactReplyOrchestrator": "domains.agent.domain.aggregates.react_agent_reply",
    "RuntimeConfig": "domains.agent.domain.aggregates.execution_context",
    "RuntimeObserver": "domains.agent.domain.aggregates.runtime_observer",
    "RuntimeToolRegistrationProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "SubagentRuntime": "domains.agent.domain.aggregates.react_agent_collaboration",
    "TextDeltaEmitter": "domains.agent.domain.aggregates.react_agent_streaming",
    "TextStreamEndNotifier": "domains.agent.domain.aggregates.react_agent_streaming",
    "ToolArgsSummaryResolver": "domains.agent.domain.aggregates.tool_call_stream",
    "ToolArgsSummaryResolverProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "ToolBatchExecutor": "domains.agent.domain.aggregates.react_agent_tool_execution",
    "ToolCallStreamAccumulator": "domains.agent.domain.aggregates.tool_call_stream",
    "ToolClassificationResolverProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "ToolDeltaEmitter": "domains.agent.domain.aggregates.react_agent_streaming",
    "ToolExecutionError": "domains.agent.domain.aggregates.react_agent_tool_execution",
    "ToolInvocationObserver": "domains.agent.domain.aggregates.tool_invocation_scope",
    "ToolInvocationScope": "domains.agent.domain.aggregates.tool_invocation_scope",
    "ToolLoopDetector": "domains.agent.domain.aggregates.tool_loop_detection",
    "ToolObservabilityMetadataResolverProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "ToolRegistry": "domains.tool.infrastructure.registry",
    "ToolResult": "domains.agent.domain.aggregates.react_agent_collaboration",
    "ToolTelemetryEmitterProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "ToolkitProtocol": "domains.agent.domain.aggregates.runtime_ports",
    "TurnCancelScope": "domains.agent.domain.aggregates.cancellation",
    "TurnCancelState": "domains.agent.domain.aggregates.cancellation",
    "bind_execution_context": "domains.agent.domain.aggregates.execution_context",
    "clear_lifecycle_status_notifiers": "domains.agent.domain.aggregates.runtime_bindings",
    "collect_model_turn": "domains.agent.domain.aggregates.react_agent_streaming",
    "get_agent_scheduler": "domains.agent.domain.aggregates.execution_context",
    "get_app_id": "domains.agent.domain.aggregates.execution_context",
    "get_app_instance": "domains.agent.domain.aggregates.execution_context",
    "get_cached_thinking_input": "domains.agent.domain.aggregates.execution_context",
    "get_current_agent_name": "domains.agent.domain.aggregates.execution_context",
    "get_current_invocation_id": "domains.agent.domain.aggregates.execution_context",
    "get_current_parent_agent_instance_id": "domains.agent.domain.aggregates.execution_context",
    "get_current_thinking_operation_id": "domains.agent.domain.aggregates.execution_context",
    "get_effective_runtime_config": "domains.agent.domain.aggregates.execution_context",
    "get_execution_context": "domains.agent.domain.aggregates.execution_context",
    "get_groupchat_round_instance_id": "domains.agent.domain.aggregates.execution_context",
    "get_hook_turn_id": "domains.agent.domain.aggregates.hook_context",
    "get_interrupt_checker": "domains.agent.domain.aggregates.runtime_bindings",
    "get_lifecycle_notifier": "domains.agent.domain.aggregates.runtime_bindings",
    "get_lifecycle_status_notifiers": "domains.agent.domain.aggregates.runtime_bindings",
    "get_message_queue": "domains.agent.domain.aggregates.runtime_bindings",
    "get_message_queue_getter": "domains.agent.domain.aggregates.runtime_bindings",
    "get_plugin_hook_bridge": "domains.agent.domain.aggregates.runtime_bindings",
    "get_project_home": "domains.agent.domain.aggregates.execution_context",
    "get_root_agent_id": "domains.agent.domain.aggregates.execution_context",
    "get_session_id": "domains.agent.domain.aggregates.execution_context",
    "get_turn_id": "domains.agent.domain.aggregates.turn_identity",
    "get_turn_scope_context": "domains.agent.domain.aggregates.scope_context",
    "interrupt_sleep": "domains.agent.domain.aggregates.interruption",
    "interruptible_sleep": "domains.agent.domain.aggregates.interruption",
    "is_session_interrupted": "domains.agent.domain.aggregates.interruption",
    "new_turn_id": "domains.agent.domain.aggregates.cancellation",
    "register_lifecycle_status_notifier": "domains.agent.domain.aggregates.runtime_bindings",
    "remaining_tool_wait_budget": "domains.agent.domain.aggregates.tool_wait_budget",
    "reset_hook_turn_id": "domains.agent.domain.aggregates.hook_context",
    "reset_turn_id": "domains.agent.domain.aggregates.turn_identity",
    "reset_turn_scope_context": "domains.agent.domain.aggregates.scope_context",
    "run_react_reply": "domains.agent.domain.aggregates.react_agent_reply",
    "set_cached_thinking_input": "domains.agent.domain.aggregates.execution_context",
    "set_current_agent_name": "domains.agent.domain.aggregates.execution_context",
    "set_current_invocation_id": "domains.agent.domain.aggregates.execution_context",
    "set_current_thinking_operation_id": "domains.agent.domain.aggregates.execution_context",
    "set_hook_turn_id": "domains.agent.domain.aggregates.hook_context",
    "set_interrupt_checker": "domains.agent.domain.aggregates.runtime_bindings",
    "set_lifecycle_notifier": "domains.agent.domain.aggregates.runtime_bindings",
    "set_message_queue_getter": "domains.agent.domain.aggregates.runtime_bindings",
    "set_plugin_hook_bridge": "domains.agent.domain.aggregates.runtime_bindings",
    "set_root_agent_id": "domains.agent.domain.aggregates.execution_context",
    "set_turn_id": "domains.agent.domain.aggregates.turn_identity",
    "set_turn_scope_context": "domains.agent.domain.aggregates.scope_context",
    "tool_wait_budget_scope": "domains.agent.domain.aggregates.tool_wait_budget",
    "tool_wait_bugdget_scope": "domains.agent.domain.aggregates.tool_wait_budget",}


def __getattr__(name):
    import importlib

    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
