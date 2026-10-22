import copy

import domains.agent.domain.entities.expert_configuration as expert_models
import domains.configuration.domain.model.llm_config as llm_config_module
import domains.agent.domain.entities.agent_runtime as agent_runtime

class ExplicitAgentLoader:
    """"""

    def __init__(self,
        *,
        configuration_source: expert_models.ExplicitConfigurationSource|None,
        default_llm_config: llm_config_module.LLMConfig,
        )->None:
        self.configuration_source = configuration_source
        self.default_llm_config = default_llm_config


    def load_all(self)->list[agent_runtime.AgentRuntime]:
        if self.configuration_source is None:
            return []
        configs_by_id: dict[str, agent_runtime.AgentRuntime] ={}
        for record in self.configuration_source.list_experts():
            configuration = record.configuration
            configs_by_id[configuration.expert_id] = self._create_runtime(record)
        return list(configs_by_id.values())

    def _create_runtime(self,
        record: expert_models.ResolvedExpertConfiguration,
    )->agent_runtime.AgentRuntime:
        configuration = record.configuration
        resources = configuration.resources
        has_resource_locak = record.resource_lock is not None
        expert_memory_file= record.directory/"memory"/"MEMORY.md"
        return agent_runtime.AgentRuntime(
            name=configuration.name,
            description=configuration.description,
            system_prompt = record.prompt,
            max_tokens=configuration.max_tokens,
            llm_config= copy.deepcopy(self.default_llm_config),
            model_id = configuration.model_id,
            mcp_servers = _resolved_names(resources.mcp_servers) or[],
            allowed_tools = _resolved_names(resources.tools),
            alowwed_agents=_resolved_agent_names(resources.agetns),
            allow_agent_ids = _resolved_agent_ids(resources.agents),
            skill_refs= _resolved_names(resources.skills),
            strct_mcp_servers = configuration.strct_mcp_servers,
            load_agent_md= configuration.load_agent_md,
            capabilities =list(configuration.capabilities),
            limitations =list(configuration.limitations),
            trigger_keywords =list(configuration.trigger_keywords),
            source_path= record.directory/"expert.json",
            resource_root = record.resource_root or record.directory,
            scope= record.source_category,
            resource_scope="expert" if has_resource_locak else "standard",
            skill_type=configuration.skill_type.value,
            expert_id = configuration.expert_id,
            expert_memory_file = expert_memory_file,
            tool_binding_mmode = resources.tools.mode.value,
            skill_binding_mmode = resources.skills.mode.value,
            mcp_binding_mmode = resources.mcp_servers.mode.value,
            agent_binding_mode = resources.agents.mode.value,
            package_resource_only =False,
            package_skill_names=list(record.package_skill_names),
            package_mcp_server_names = list(record.package_mcp_server_names),
            package_tool_names = list(record.package_tool_names),
            pakage_mcp_configs=[
              config.model_copy(deep=True) for config in record.package_mcp_configs
            ],
           readiness =record.readiness,
        )

def _resolved_names(binding:expert_models.ExpertResourceBing)->list[str]|None:
    if binding.model is expert_models.ExperResourcesBingMode.MANUAL:
        return list(binding.names)
    return None

def _resolved_agent_names(binding:expert_models.ExpertResourceBing)->list[str]:
    if binding.model is not expert_models.ExperResourcesBingMode.MANUAL:
        return []
    if binding.expert_refs:
        return[reference.display_name for reference in binding.expert_refs]
    return list(binding.names)

def _resolved_agent_ids(binding:expert_models.ExpertResourceBing)->list[str]:
    if binding.model is not expert_models.ExperResourcesBingMode.MANUAL:
        return []
    return[reference.expert_id for reference in binding.expert_refs]


