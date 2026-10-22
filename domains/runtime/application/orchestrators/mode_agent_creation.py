"""按模式创建 Agent 的服务。

不同应用模式（``solo`` / ``delegate`` / ``groupchat`` / ``pipeline`` …）需要
不同形态的 agent 拓扑：

- ``solo``：只创建一个 agent；
- ``delegate``：创建主 agent + 一组可被委托的子 agent；
- ``groupchat``：创建多个对等 agent（由 :mod:`groupchat_agent_builder` 负责）。

本服务是这一分派的入口。``AgentOrchestrator`` 构造时持有一个实例。

:meth:`ModeAgentCreationService.create` 只做两件事——**拓扑决策**与**委托构建**：
真正的「配置 -> :class:`ReActAgent`」由 ``AgentFactory`` 完成。没有任何一方能
构建时返回空列表并记 warning，由调用方决定降级方式，而不是让启动失败。
"""

from __future__ import annotations

import inspect
import logging
import typing

import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.application.loader.root_agent_loader as root_agent_loader
import domains.configuration.domain.model.app_mode as app_mode_configuration
import domains.plugin.domain as plugin_contracts
import domains.runtime.application.orchestrators.agent_orchestration_contacts as orchestration_contracts
import domains.runtime.application.orchestrators.mode_orchestration as mode_orchestration
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.agent.application.loader.agent_factory as agent_factory_module
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.plugin.application.request_context as plugin_request_context
import domains.agent.domain.value_objects.conversation_context as conversation_context_module
import domains.agent.domain.entities.agent_role as agent_role_module
logger = logging.getLogger(__name__)

__all__ = ["ModeAgentCreationService"]

#: 编排者槽位类型；这类槽位由宿主的专用构建方法负责。
_ORCHESTRATOR_AGENT_TYPE = "orchestrator"

#: 群聊是独立拓扑，单独走 :mod:`groupchat_agent_builder`，本服务不处理。
_GROUPCHAT_MODE = "groupchat"

#: 未指定模式时的约定默认值，与 ``AgentOrchestrator._current_mode`` 保持一致。
_FALLBACK_MODE = "delegate"

_LOG_PREFIX = "[MODE-CREATE] "


class ModeAgentCreationService:
    """按应用模式创建 agent 集合。"""

    __slots__ = ("_supported_modes",)

    #: 当前已实现创建逻辑的模式；其余模式记 warning 后仍走通用构建路径。
    _DEFAULT_SUPPORTED_MODES: frozenset[str] = (
        frozenset(app_mode_configuration.BUILTIN_APP_MODES) - {_GROUPCHAT_MODE}
    )

    def __init__(self, supported_modes: typing.Iterable[str] | None = None) -> None:
        self._supported_modes = frozenset(supported_modes or self._DEFAULT_SUPPORTED_MODES)

    @property
    def supported_modes(self) -> frozenset[str]:
        """当前支持的模式集合。"""
        return self._supported_modes

    def supports(self, mode: str | None) -> bool:
        """指定模式是否已有创建实现。"""
        return str(mode or "") in self._supported_modes

    async def create(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        *,
        slot: plugin_contracts.AgentSlot,
        sub_agents: list[agent_runtime_module.AgentRuntime],
        root_agent_config:root_agent_config_module.RootAgentConfig= None,
        agent_factory: agent_factory_module.AgentFactory,
        on_progress: orchestration_contracts.ProgressCallback|None = None,
        mode_decl: plugin_contracts.ModeDeclaration |None=None,
    ) -> agent_module.ReActAgent:
        if slot.agent_type=="orchestrator":
            return await host._build_orchestrator_agent(
                agent_factory=agent_factory,
                root_agent_config = root_agent_config,
                sub_agents=sub_agents,
                on_progress= on_progress,
            )
        effective_mode = mode_decl or host.app.active_mode_declaration
        unique_name=self._resolve_agent_id(host,slot,effective_mode)
        runtime_config =self._build_runtime_config(
            host,
            slot=slot,
            sub_agents=sub_agents,
            root_agent_config=root_agent_config,
            agent_factory=agent_factory,
            # ``_build_runtime_config`` 的形参是 ``model_decl``（历史拼写），
            # 而本方法的对外形参是 ``mode_decl``，边界上在这里对齐一次。
            model_decl=effective_mode,
        )
        plugin_request_context.set_current_slot(slot)
        return await agent_factory.create_react_agent(
            sub_agent=runtime_config,
            override_name= unique_name,
            on_progress=on_progress,
            observer=host.app.observer,
        )

    def _resolve_agent_id(
        self,
        host: typing.Any,
        slot: typing.Any,
        effective_mode: typing.Any,
    ) -> str:
        """确定本次构建的 agent 名，并保证在宿主的运行时容器里唯一。

        重名的后果很隐蔽：后建的会把先建的从 ``agent_runtimes`` 顶掉，于是整棵
        agent 树里会凭空少一个节点，而委托链路仍然「看起来正常」。所以这里显式
        加序号去重，而不是静默覆盖。

        :param host: 宿主（``AgentOrchestrator`` 或宿主应用）。
        :param slot: 槽位声明，名字的来源。
        :param effective_mode: 生效的模式声明，用于取回退根槽位名。
        :return: 可用的 agent 名。
        """
        base = str(getattr(slot, "name", "") or "")
        if not base:
            base = str(getattr(effective_mode, "root_agent", "") or "") or "planner"

        app = getattr(host, "app", None) or host
        runtimes = getattr(app, "agent_runtimes", None)
        if not isinstance(runtimes, dict) or base not in runtimes:
            return base

        suffix = 2
        while f"{base}-{suffix}" in runtimes:
            suffix += 1
        unique = f"{base}-{suffix}"
        logger.debug(
            "%sagent name %r already registered; using %r instead", _LOG_PREFIX, base, unique
        )
        return unique

    def _build_runtime_config(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        *,
        slot: plugin_contracts.AgentSlot,
        sub_agents:list[agent_runtime_module.AgentRuntime] ,
        root_agent_config: root_agent_config_module.RootAgentConfig,
        agent_factory: agent_factory_module.AgentFactory,
        model_decl: plugin_contracts.ModeDeclaration|None,
    ) -> agent_runtime_module.AgentRuntime:
        resolved_names = host._resolve_visible_subagent_names_for_slot(
                slot,
                sub_agents,
                model_decl,
            )
        config = agent_runtime_module.AgentRuntime(
            name=slot.name,
            description=f"{slot.name} agent",
            max_tokens= host._resolve_root_max_tokens(
                root_agent_config,
                agent_factory,
            ),
            system_prompt= root_agent_config.system_prompt,
            llm_config= host.app.llm_config,
            mcp_servers=(list(root_agent_config.mcp_servers)if slot.include_mcp_tools else []),
            allowed_tools=(
                list(root_agent_config.allowed_tools) if root_agent_config.allowed_tools is not None else None
            ),
            skill_refs=list(root_agent_config.skill_refs) if root_agent_config.skill_refs is not None else None,
            strict_mcp_servers=root_agent_config.strict_mcp_servers,
            load_agents_md= root_agent_config.load_agents_md,
            allowed_agents=mode_orchestration.refine_allowed_agents(
                getattr(model_decl, "name", None) or host.app.app_mode,
                model_decl,
                slot.name,
                resolved_names,
            ),
            include_agent_tools = slot.include_agent_tools,
            agent_slot=slot,
        )
        config.agent_role= agent_role_module.AgentRole.resolve(
            config.agent_role,
            slot,
        )
        return config





