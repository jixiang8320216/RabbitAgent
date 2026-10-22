"""编排层与宿主应用之间的契约（port）。

编排器（``AgentOrchestrator`` / ``SessionOrchestrator`` / ``PluginOrchestrator``）
需要访问宿主的装配产物：配置、agent 树、角色上下文、各类注册表、持久化句柄等。
这些能力由 ``RabbitApplicationCompositionState`` 提供。

契约以 ``typing.Protocol`` 表达，好处是：编排器只依赖「属性名 + 语义」，
宿主无需显式继承即可满足（结构子类型）。为了让静态检查器与
``isinstance`` 都能工作，协议标记为 ``runtime_checkable``。

关于命名：调用点出现过两种拼写——``agent_orchestrator.py`` 用
``AgentOrchestrationAppPort``，``rabbit_application.py`` 用
``AgentOrchestratIONAppPort``（中间多一个大写 ``ION``）。两者指向同一个契约，
这里同时导出，避免任一处 ``AttributeError``。

属性声明采用最小必要原则：只列出编排层真实访问的部分，并统一标注为
``typing.Any``——契约的价值在于「名字存在且语义明确」，而不是在类型系统里
把宿主的所有内部结构复述一遍（那会产生大量循环导入与脆弱耦合）。
"""

from __future__ import annotations
import collections
import typing

import domains.plugin.domain as plugin_contracts
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.application.loader.agent_factory as agent_factory_module
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.agent.domain.entities.role_context as role_context_module
import domains.plan.application.plan_runtime as plan_runtime_module
import domains.mcp.infrastructure.client_runtime as client_runtime
import domains.mcp.domain.elicitation as elicitation
import domains.agent.application as groupchat_application

# 编排器在装配签名里引用 ``ProgressCallback``（见 ``agent_orchestrator``）；
# 它即工具链进度回调类型，这里直接给出与其一致的别名，避免调用点 AttributeError。
ProgressCallback = collections.abc.Callable[[str], collections.abc.Awaitable[None]]

__all__ = [
    "AgentOrchestrationAppPort",
    "AgentOrchestratIONAppPort",
    "AgentConfigsPort",
    "AgentContextsPort",
]


from domains.configuration.infrastructure.adapter import config_manager as configuration

from domains.configuration.domain.model import app_config as application_configuration
from domains.configuration.domain.runtime_state import snapshot as session_snapshot

ProcessCallback= collections.abc.Callable[[str], collections.abc.Awaitable[None]]

class  AgentOrchestrationHost(typing.Protocol):

    app: AgentOrchestrationAppPort

    def _resolve_skill_agent_slot(self,
        sub_agent_config: agent_runtime_module.AgentRuntime,
    )->plugin_contracts.AgentSlot:...

    def _resolve_root_max_tokens(
        self,
        root_agent_config: agent_runtime_module.AgentRuntime,
        agent_factory: agent_factory_module.AgentFactory,
        fallback:int=150_000,
    )->int:...

    def _resolve_visible_subagent_for_slot(
        self,
        slot: plugin_contracts.AgentSlot,
            all_sub_agents: list[agent_runtime_module.AgentRuntime],
    )->typing.List[agent_runtime_module.AgentRuntime]:...

    def _resolve_visible_subagent_names_for_slot(
        self,
        slot: plugin_contracts.AgentSlot,
        all_sub_agents:list[agent_runtime_module.AgentRuntime],
        model_decl:plugin_contracts.ModeDeclaration|None=None,
    )->list[str]:...

    async def _build_orchestrator_agent(
        self,
        agent_factory: agent_factory_module.AgentFactory,
        root_agent_config: root_agent_config_module.RootAgentConfig,
        sub_agents: list[agent_runtime_module.AgentRuntime],
        on_progress: ProcessCallback |None=None,
        utility_tool_names: list[tuple[str,str]] |None=None,
    )->agent_module.ReActAgent:...


class AgentOrchestrationAppPort(typing.Protocol):
    """编排层所需的宿主应用契约。"""

    # --- 配置与运行形态 ---
    config: application_configuration.AppConfig
    config_manager: configuration.ConfigManager
    app_mode: str
    project_system_prompt: str | None
    user_project_home: str | None
    sub_agents_dir: str
    root_agent_prompts_dir: str

    role_context: role_context_module.RoleContext
    active_mode_declaration: plugin_contracts.ModeDeclaration|None
    active_execution_plan: typing.Any|None
    current_default_agent_id: str | None
    default_agent_history: dict[str, list[session_snapshot.DefaultRecord]]
    plan_runtime: plan_runtime_module.PlanRuntime |None
    agent_lifecycle: None
    delegate_agent: agent_module.ReActAgent|None
    root_agent_config: root_agent_config_module.RootAgentConfig|None
    sub_agents: list[agent_runtime_module.AgentRuntime]
    skill_catalog: dict[str, agent_runtime_module.AgentRuntime]
    agent_factory: agent_factory_module.AgentFactory|None
    mcp_manager: client_runtime.MCPManager|None
    _chat_board: groupchat_application.GroupChatBoard|None
    _current_invoker_agent: agent_module.ReActAgent|None
    _default_agent_id_override: str|None
    _base_system_prompt: str|None
    _session_orch: elicitation.MCPElicitationHandler


    # --- 协议 ---
    def refresh_system_prompt(self) -> typing.Any:
        """刷新系统提示词（角色上下文变更后调用）。"""
        ...


# 调用点 ``rabbit_application.py:55`` 使用的拼写（中间多一个大写 ``ION``）。
# 定义为子类而非简单别名：这样两处 ``typing.cast`` 都能拿到可用的类型对象，
# 且 IDE 跳转仍指向本模块。
class AgentOrchestratIONAppPort(AgentOrchestrationAppPort, typing.Protocol):
    """``AgentOrchestrationAppPort`` 的拼写变体。"""


@typing.runtime_checkable
class AgentConfigsPort(typing.Protocol):
    """Agent 配置视图契约（``self.agent_configs`` 的消费面）。"""

    def load_agent_configs(
        self,
        selected_expert_ids: typing.Sequence[str] | None = None,
        selectec_skill_names: typing.Sequence[str] | None = None,
    ) -> typing.Any:
        ...


@typing.runtime_checkable
class AgentContextsPort(typing.Protocol):
    """Agent 上下文视图契约（``self.contexts`` 的消费面）。"""

    def get(self, agent_id: str) -> typing.Any:
        ...
