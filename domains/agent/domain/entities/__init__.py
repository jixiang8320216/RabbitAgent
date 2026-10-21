"""domains.agent.domain.entities 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentAlreadyRunning": "domains.agent.domain.entities.agent_scheduling",
    "AgentAutoCompactSuppression": "domains.agent.domain.entities.agent_auto_compact_suppression",
    "AgentCallState": "domains.agent.domain.entities.agent_call_state",
    "AgentCompressionBusyTimeout": "domains.agent.domain.entities.agent_scheduling",
    "AgentContext": "domains.agent.domain.entities.agent_context",
    "AgentContextRecord": "domains.agent.domain.entities.agent_context",
    "AgentExecutionPaths": "domains.agent.domain.entities.agent_execution_paths",
    "AgentInstance": "domains.agent.domain.entities.agent_instance",
    "AgentInterruptionReason": "domains.agent.domain.entities.agent_context",
    "AgentInvocationRecord": "domains.agent.domain.entities.agent_invocation_record",
    "AgentMessageReservation": "domains.agent.domain.entities.agent_queued_message",
    "AgentNode": "domains.agent.domain.entities.agent_tree_node",
    "AgentQueuedMessage": "domains.agent.domain.entities.agent_queued_message",
    "AgentRegistryPort": "domains.agent.domain.entities.agent_context",
    "AgentResourceBindingMode": "domains.agent.domain.entities.agent_runtime",
    "AgentRole": "domains.agent.domain.entities.agent_role",
    "AgentRuntime": "domains.agent.domain.entities.agent_runtime",
    "AgentRuntimeApp": "domains.agent.domain.entities.agent_runtime",
    "AgentRuntimeModelConfig": "domains.agent.domain.entities.agent_runtime",
    "AgentRuntimeSkillType": "domains.agent.domain.entities.agent_runtime",
    "AgentRuntimeSpec": "domains.agent.domain.entities.agent_runtime_spec",
    "AgentStatus": "domains.agent.domain.entities.agent_context",
    "AgentTranscriptEvent": "domains.agent.domain.entities.agent_transcript",
    "AgentTranscriptEventType": "domains.agent.domain.entities.agent_transcript",
    "AgentTranscriptMetadata": "domains.agent.domain.entities.agent_transcript",
    "AgentWaitMode": "domains.agent.domain.entities.agent_context",
    "AssociatedExpertRef": "domains.agent.domain.entities.expert_configuration",
    "AvailableModeDescriptor": "domains.agent.domain.entities.model_registration",
    "COMPRESSION_GATE_POLL_INTERVAL_SECONDS": "domains.agent.domain.entities.agent_scheduling",
    "COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS": "domains.agent.domain.entities.agent_scheduling",
    "EXPERT_SCHEMA_VERSION": "domains.agent.domain.entities.expert_configuration",
    "ExperResourcesBingMode": "domains.agent.domain.entities.expert_configuration",
    "ExpertConfiguration": "domains.agent.domain.entities.expert_configuration",
    "ExpertPackageMetadata": "domains.agent.domain.entities.expert_configuration",
    "ExpertReadiness": "domains.agent.domain.entities.expert_configuration",
    "ExpertReadinessDiagnostic": "domains.agent.domain.entities.expert_configuration",
    "ExpertReadinessStatus": "domains.agent.domain.entities.expert_configuration",
    "ExpertResourceBing": "domains.agent.domain.entities.expert_configuration",
    "ExpertResourceBings": "domains.agent.domain.entities.expert_configuration",
    "ExpertResourceLock": "domains.agent.domain.entities.expert_configuration",
    "ExpertSkillType": "domains.agent.domain.entities.expert_configuration",
    "ExplicitConfigurationSource": "domains.agent.domain.entities.expert_configuration",
    "InputOverflowResult": "domains.agent.domain.entities.input_overflow_result",
    "JsonObject": "domains.agent.domain.entities.agent_instance",
    "ModelRegistrationSource": "domains.agent.domain.entities.model_registration",
    "RegisteredMode": "domains.agent.domain.entities.model_registration",
    "ResolvedExpertConfiguration": "domains.agent.domain.entities.expert_configuration",
    "RoleContext": "domains.agent.domain.entities.role_context",
    "RootAgentConfig": "domains.agent.domain.entities.root_agent_config",
    "SYNC_AGENT_WAIT_TIMEOUT_SECONDS": "domains.agent.domain.entities.agent_scheduling",}


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
