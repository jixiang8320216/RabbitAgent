"""domains.runtime 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentBootstrapService": "domains.runtime.application.orchestrators.agent_bootstrap",
    "AgentConfigsPort": "domains.runtime.application.orchestrators.agent_orchestration_contacts",
    "AgentConfigurationLoadingService": "domains.runtime.application.orchestrators.agent_configuration_loading",
    "AgentContextsPort": "domains.runtime.application.orchestrators.agent_orchestration_contacts",
    "AgentOrchestratIONAppPort": "domains.runtime.application.orchestrators.agent_orchestration_contacts",
    "AgentOrchestrationAppPort": "domains.runtime.application.orchestrators.agent_orchestration_contacts",
    "AgentOrchestrationHost": "domains.runtime.application.orchestrators.agent_orchestration_contacts",
    "AgentOrchestrator": "domains.runtime.application.orchestrators.agent_orchestrator",
    "COMPAT_PLACEHOLDER_SPEC_FLAG": "domains.runtime.application.rabbit_application_composition",
    "GenerateTopic": "domains.runtime.application.orchestrators.session_topic",
    "GroupChatAgentBuilder": "domains.runtime.application.orchestrators.groupchat_agent_builder",
    "HookAgentCommonInput": "domains.runtime.application.hook_lifecycle_contracts",
    "HookCommonInput": "domains.runtime.application.hook_lifecycle_contracts",
    "HookLifecycleCoordinator": "domains.runtime.application.hook_lifecycle",
    "HookLifecycleMetadata": "domains.runtime.application.hook_lifecycle_contracts",
    "MCPProjectionResolver": "domains.runtime.application.orchestrators.agent_bootstrap",
    "ModeAgentCreationService": "domains.runtime.application.orchestrators.mode_agent_creation",
    "ModeExecutionPlan": "domains.runtime.application.orchestrators.mode_orchestration",
    "ModeExecutionResult": "domains.runtime.application.orchestrators.mode_orchestration",
    "ModeOrchestrationService": "domains.runtime.application.orchestrators.mode_orchestration",
    "MODE_EXECUTION_PLANS": "domains.runtime.application.orchestrators.mode_orchestration",
    "StageSpec": "domains.runtime.application.orchestrators.mode_orchestration",
    "StageTrace": "domains.runtime.application.orchestrators.mode_orchestration",
    "ModelProfileConfigManager": "domains.runtime.application.orchestrators.session_turn_contracts",
    "Participant": "domains.runtime.application.participant_registry",
    "ParticipantRegistry": "domains.runtime.application.participant_registry",
    "PlginLoadPlan": "domains.runtime.application.orchestrators.plugin_orchestrator",
    "PluginHookBridgeAdapter": "domains.runtime.application.plugin_hook_bridge",
    "PluginOrchestrationHost": "domains.runtime.application.orchestrators.plugin_orchestrator",
    "PluginOrchestrator": "domains.runtime.application.orchestrators.plugin_orchestrator",
    "PluginSettings": "domains.runtime.application.orchestrators.plugin_orchestrator",
    "ProcessCallback": "domains.runtime.application.orchestrators.agent_orchestration_contacts",
    "RabbitApplication": "domains.runtime.application.rabbit_application",
    "RabbitApplicationCompositionState": "domains.runtime.application.rabbit_application_composition",
    "RoleContextApplicationService": "domains.runtime.application.role_context_service",
    "SaveAppState": "domains.runtime.application.orchestrators.session_topic",
    "SessionAppService": "domains.runtime.application.session_management",
    "SessionHookLifecycleCoordinator": "domains.runtime.application.orchestrators.session_hook_lifecycle",
    "SessionHookTurnCoordinator": "domains.runtime.application.orchestrators.session_hook_turns",
    "SessionIncrementalSaveCoordinator": "domains.runtime.application.orchestrators.session_incremental_save",
    "SessionLifecycleCoordinator": "domains.runtime.application.orchestrators.session_lifecyle",
    "SessionOrchestrator": "domains.runtime.application.orchestrators.session_orchestrator",
    "SessionStateApplication": "domains.runtime.application.orchestrators.session_state_contracts",
    "SessionStateHydrator": "domains.runtime.application.orchestrators.session_state_hydrator",
    "SessionStateWriter": "domains.runtime.application.orchestrators.session_state_writer",
    "SessionTopicApplication": "domains.runtime.application.orchestrators.session_topic",
    "SessionTopicCoordinator": "domains.runtime.application.orchestrators.session_topic",
    "SessionTurnCheckpointCoordinator": "domains.runtime.application.orchestrators.session_turn_checkpoint",
    "SessionTurnExecutor": "domains.runtime.application.orchestrators.session_turn_execution",
    "TopicMessage": "domains.runtime.application.orchestrators.session_topic",
    "TopicSeed": "domains.runtime.application.orchestrators.session_topic",
    "TurnCheckpoint": "domains.runtime.application.orchestrators.session_turn_checkpoint",
    "TurnContextPersistenceIndeterminateError": "domains.runtime.application.orchestrators.session_turn_contracts",
    "TurnContextResetError": "domains.runtime.application.orchestrators.session_turn_execution",
    "TurnExecutionApplication": "domains.runtime.application.orchestrators.session_turn_contracts",
    "TurnExecutionOwner": "domains.runtime.application.orchestrators.session_turn_contracts",
    "UtilityToolRef": "domains.runtime.application.orchestrators.groupchat_agent_builder",
    "bind_app_request_context": "domains.runtime.application.request_context",
    "compose_plugin_load_plan": "domains.runtime.application.orchestrators.plugin_orchestrator",
    "describe_execution_plan": "domains.runtime.application.orchestrators.mode_orchestration",
    "refine_allowed_agents": "domains.runtime.application.orchestrators.mode_orchestration",
    "resolve_execution_plan": "domains.runtime.application.orchestrators.mode_orchestration",
    "ensure_profile_stable_id": "domains.runtime.application.runtime_model_config",
    "event_to_topic_message": "domains.runtime.application.orchestrators.session_topic",
    "iter_topic_message_dicts": "domains.runtime.application.orchestrators.session_topic",
    "load_project_plugin_configuration": "domains.runtime.application.orchestrators.plugin_orchestrator",
    "message_content_text": "domains.runtime.application.orchestrators.session_topic",
    "normalize_profiles_with_stable_ids": "domains.runtime.application.runtime_model_config",
    "normalize_topic_messages": "domains.runtime.application.orchestrators.session_topic",
    "project_group_chat_selected_expert_ids": "domains.runtime.application.orchestrators.session_state_writer",
    "project_runtime_plan": "domains.runtime.application.orchestrators.session_state_writer",}


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
