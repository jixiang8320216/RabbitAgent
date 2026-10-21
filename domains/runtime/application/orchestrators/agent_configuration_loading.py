"""Agent 配置加载服务。

职责：把「宿主配置 + 目录约定」解析为「一组可直接构造的 Agent 配置」。
``RabbitApplication._load_agent_configs`` 最终落到这里，
``AgentOrchestrator`` 在构造时持有一个实例。

当前阶段只保证**可构造、可调用、可降级**：

- 无参构造（``AgentConfigurationLoadingService()``）；
- :meth:`load` 在依赖缺失时返回 ``None`` 而不是抛错——agent 配置缺失意味着
  本次会话无法创建 agent，由上层决定如何提示，而不是让进程崩溃。
- 真正的扫描逻辑（读 ``sub_agents_dir``、解析根 agent 提示词目录、合并专家/
  skill 选择）留待后续实现，这里给出完整的入参契约与返回结构说明。
"""

from __future__ import annotations

import collections.abc
import logging
import typing

import domains.runtime.application.orchestrators.agent_orchestration_contacts as orchestration_contracts
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.agent.application.loader.root_agent_loader as root_agent_loader_module
import domains.agent.application.loader.explicit_agent_loader as explicit_agent_loader_module
import domains.agent.domain.entities.agent_runtime as  agent_runtime_module
import domains.agent.application.registry.model_registry as model_registry

logger = logging.getLogger(__name__)

__all__ = ["AgentConfigurationLoadingService"]


class AgentConfigurationLoadingService:
    """加载 Agent 配置。"""

    __slots__ = ("_project_home",)

    def __init__(self, project_home: typing.Any = None) -> None:
        """:param project_home: 可选的项目根目录，用于定位项目级 agent 配置。"""
        self._project_home = project_home

    @property
    def project_home(self) -> typing.Any:
        return self._project_home

    def load_root(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        *,
        root_agent_loader_type: type[root_agent_loader_module.RootAgentLoader],
        mode:str,
        )-> root_agent_config_module.RootAgentConfig|None:
        root_agent_loader=root_agent_loader_type(
            config_dir = host.app.root_agent_prompts_dir,
            config_manager=host.app.config_manager,
            mode_registry=host.app.mode_registry,
            project_home=host.app.user_project_home,
        )
        return root_agent_loader.load(mode=mode)

    def load(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        *,
        sub_agent_loader_type: type[explicit_agent_loader_module.ExplicitAgentLoader],
        root_agent_loader_type: type[root_agent_loader_module.RootAgentLoader],
        selected_expert_ids: collections.abc.Sequence[str]|None= None,
        selectec_skill_names: collections.abc.Sequence[str]|None= None,
    ) -> tuple[list[agent_runtime_module.AGgentRuntime], root_agent_config_module.RootAgentConfig| None] :
        sub_agent_configs = self._load_explicit_catalog(
            host,
            sub_agent_loader_type=sub_agent_loader_type,
        )
        if selected_expert_ids is not None and selectec_skill_names is not None:
            raise ValueError("GroupChat selection cannont contain both stable IDS and legacy names")
        if selected_expert_ids is not None:
            sub_agent_configs = self._select_groupchat_experts(
                host,
                sub_agent_configs,
                selected_expert_ids,
            )
        elif selectec_skill_names is not None:
            sub_agent_configs = self._select_legacy_groupchat_names(
                host,
                sub_agent_configs,
                selectec_skill_names,
            )
        if not sub_agent_configs:
            logger.warning("no sub agent configurations found.")
        root_agent_config = self.load_root(
            host,
            root_agent_loader_type=root_agent_loader_type,
            mode = host.app.app_mode,
        )
        if root_agent_config is None:
            logger.error("Planner configuration no found.")
            return [],None
        return sub_agent_configs, root_agent_config

    def load_explicit_catalog(
        self,
        host:orchestration_contracts.AgentOrchestrationHost,
        *,
        sub_agent_loader_type: type[explicit_agent_loader_module.ExplicitAgentLoader],
    )->list[agent_runtime_module.AgentRuntime]:
        if host.app.config.expert_configuration_source is None:
            raise RuntimeError(
                "Cannot recover a legacy agent catalog without an ExpertConfiguration source."
            )
        return self._load_explicit_catalog(
            host,
            sub_agent_loader_type=sub_agent_loader_type,
        )

    @staticmethod
    def _load_explicit_catalog(
        host: orchestration_contracts.AgentOrchestrationHost,
        *,
        sub_agent_loader_type: type[explicit_agent_loader_module.ExplicitAgentLoader],
    )->list[agent_runtime_module.AgentRuntime]:
        sub_agent_loader = sub_agent_loader_type(
            configuration_source = host.app.config.expert_configuration_source,
            default_llm_config = host.app.llm_config,
        )
        return sub_agent_loader.load_all()


    def __repr__(self) -> str:
        return f"AgentConfigurationLoadingService(project_home={self._project_home!r})"
