"""domains.agent.application 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentCallLifecycleHookAttacher": "domains.agent.application.hook.call_lifecycle_hooks",
    "AgentCallState": "domains.agent.application.context.call_state",
    "AgentDiagnosticContext": "domains.agent.application.lifecycle.runtime_composition",
    "AgentExecutionPathResolver": "domains.agent.application.context.execution_store",
    "AgentExecutionStore": "domains.agent.application.context.execution_store",
    "AgentFactory": "domains.agent.application.loader.agent_factory",
    "AgentFactoryApplicationPort": "domains.agent.application.loader.agent_factory",
    "AgentIdGenerator": "domains.agent.application.lifecycle.id_generator",
    "AgentInstanceManager": "domains.agent.application.lifecycle.instance_manager",
    "AgentInvocationTracker": "domains.agent.application.lifecycle.invocation_tracker",
    "AgentMcpToolRegistrar": "domains.agent.application.toolkit.mcp_tool_registration",
    "AgentMessage": "domains.agent.application.context.message_queue",
    "AgentMessageQueue": "domains.agent.application.context.message_queue",
    "AgentPreReasoningHookAttacher": "domains.agent.application.hook.pre_reasoning_hooks",
    "AgentRecoveryService": "domains.agent.application.lifecycle.recovery_service",
    "AgentRuntimeApplicationPort": "domains.agent.application.lifecycle.runtime_composition",
    "AgentRuntimeCompositionHost": "domains.agent.application.lifecycle.runtime_composition",
    "AgentRuntimeHookBuilder": "domains.agent.application.hook.runtime_hook_builder",
    "AgentRuntimeOptions": "domains.agent.application.lifecycle.runtime_composition",
    "AgentScheduler": "domains.agent.application.scheduling.scheduler",
    "AgentSchedulerApplicationPort": "domains.agent.application.scheduling.scheduler",
    "AgentTaskCompleteHookAttacher": "domains.agent.application.hook.task_complete_hooks",
    "AgentTaskFactory": "domains.agent.application.scheduling.scheduler",
    "AgentToolEventHookAttacher": "domains.agent.application.hook.tool_event_hooks",
    "AgentToolkitApplicationPort": "domains.agent.application.toolkit.toolkit_builder",
    "AgentToolkitAssembly": "domains.agent.application.toolkit.toolkit_assembly",
    "AgentToolkitBuilder": "domains.agent.application.toolkit.toolkit_builder",
    "AgentToolkitProgressCallback": "domains.agent.application.toolkit.toolkit_builder",
    "AgentTree": "domains.agent.application.scheduling.agent_tree",
    "AgentTreeApplication": "domains.agent.application.hook.runtime_hook_contracts",
    "AppModeContract": "domains.agent.application.hook.runtime_hook_support",
    "AppModeSource": "domains.agent.application.hook.runtime_hook_contracts",
    "COMPRESSION_GATE_MAX_WAIT_SECONDS": "domains.agent.application.scheduling.scheduler",
    "COMPRESSION_GATE_POLL_INTERVAL_SECONDS": "domains.agent.application.scheduling.scheduler",
    "COMPRESSION_GATE_POLL_SECONDS": "domains.agent.application.scheduling.scheduler",
    "COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS": "domains.agent.application.scheduling.scheduler",
    "ClearCallback": "domains.agent.application.context.message_queue",
    "CommandObservabilityMetadataResolver": "domains.agent.application.toolkit.observability_metadata",
    "ConsumedCallback": "domains.agent.application.context.message_queue",
    "ContextOptimizerBootstrap": "domains.agent.application.context.context_optimizer_adapter",
    "ContextOptimizerBootstrapper": "domains.agent.application.context.context_optimizer_adapter",
    "ContextOptimizerConfigurationSource": "domains.agent.application.context.context_optimizer_adapter",
    "DEBATE_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "DEFAULT_ROOT_AGENT_MODEL": "domains.agent.application.loader.root_agent_loader",
    "DEFAULT_SYNC_WAIT_TIMEOUT_SECONDS": "domains.agent.application.scheduling.scheduler",
    "DEFAULT_SYSTEM_PROMPT": "domains.agent.application.loader.root_agent_loader",
    "DELEGATE_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "DefaultAgentIdGenerator": "domains.agent.application.lifecycle.id_generator",
    "EnqueuedCallback": "domains.agent.application.context.message_queue",
    "ExpertCallApplicationPort": "domains.agent.application.loader.expert_call_service",
    "ExpertCallService": "domains.agent.application.loader.expert_call_service",
    "ExpertCatalogApplication": "domains.agent.application.loader.expert_call_service",
    "ExplicitAgentLoader": "domains.agent.application.loader.explicit_agent_loader",
    "FormatterConfigurationSource": "domains.agent.application.context.model_context_factory",
    "GROUPCHAT_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "GUARDED_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "GenerateResponseDecision": "domains.agent.application.hook.call_lifecycle_hooks",
    "InvocationExecutionState": "domains.agent.application.hook.runtime_hook_contracts",
    "InvocationStateApplication": "domains.agent.application.hook.runtime_hook_contracts",
    "MAX_RESUME_ATTEMPTS_PER_AGENT": "domains.agent.application.scheduling.scheduler",
    "MAX_RETAINED_RESULTS": "domains.agent.application.scheduling.scheduler",
    "McpRegistrationResult": "domains.agent.application.toolkit.mcp_tool_registration",
    "ModelContextFactory": "domains.agent.application.context.model_context_factory",
    "ModelRegistry": "domains.agent.application.registry.model_registry",
    "NullCancelScope": "domains.agent.application.scheduling.scheduler",
    "PARALLEL_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "PIPELINE_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "PlanRuntime": "domains.agent.application.lifecycle.instance_manager",
    "QueuedCallback": "domains.agent.application.context.message_queue",
    "ROLEPLAY_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "RecoveryCandidate": "domains.agent.application.lifecycle.recovery_service",
    "ReservationAdmissionCallback": "domains.agent.application.context.message_queue",
    "ReservationContextCommittedCallback": "domains.agent.application.context.message_queue",
    "RootAgentLoader": "domains.agent.application.loader.root_agent_loader",
    "SOLO_DECLARATION": "domains.agent.application.prompt.builtin_mode_declarations",
    "SessionResumeApplicationGetter": "domains.agent.application.lifecycle.session_resume_service",
    "SessionResumeService": "domains.agent.application.lifecycle.session_resume_service",
    "SessionResumeServicePort": "domains.agent.application.scheduling.scheduler",
    "SnapshotCallback": "domains.agent.application.context.message_queue",
    "SystemPromptBuilder": "domains.agent.application.prompt.system_prompt_builder",
    "ToolArgsSummaryFactory": "domains.agent.application.hook.tool_event_hooks",
    "ToolClassification": "domains.agent.application.toolkit.toolkit_assembly",
    "ToolClassifier": "domains.agent.application.toolkit.toolkit_assembly",
    "ToolHookState": "domains.agent.application.hook.runtime_hook_contracts",
    "ToolSource": "domains.agent.application.toolkit.toolkit_assembly",
    "append_plugin_system_transform": "domains.agent.application.prompt.plugin_prompt_transform",
    "clear_agent_call_state": "domains.agent.application.context.call_context",
    "create_react_agent": "domains.agent.application.lifecycle.runtime_composition",
    "create_tool_args_summary": "domains.agent.application.hook.runtime_hook_support",
    "determine_skill_and_role": "domains.agent.application.hook.runtime_hook_support",
    "get_agent_call_state": "domains.agent.application.context.call_context",
    "get_builtin_mode_definitions": "domains.agent.application.prompt.builtin_mode_declarations",
    "inspect_isawaitable": "domains.agent.application.scheduling.scheduler",
    "set_agent_call_state": "domains.agent.application.context.call_context",
    "should_attach_memory": "domains.agent.application.lifecycle.runtime_composition",
    "should_send_generate_response": "domains.agent.application.hook.runtime_hook_support",}


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
