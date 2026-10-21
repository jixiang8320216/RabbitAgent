
import typing

import domains.agent.domain.aggregates.react_agent as react_agent_module
import domains.agent.domain.value_objects.message as message_module
import domains.llm.provider.domain.runtime_configuration as provider_runtime_configuration
import domains.agent.domain.entities.agent_instance as agent_instance_module
import domains.persistence.application.persistence_plane as persistence_plane_module
import domains.agent.application.scheduling.agent_tree as agent_tree_module
import domains.runtime.application.hook_lifecycle as hook_lifecycle_module
import domains.agent.application.loader.agent_factory as agent_factory_module


class TurnContextPersistenceIndeterminateError(RuntimeError):
    """"""


class ModelProfileConfigManager(typing.Protocol):

    def get_llm_config_profiles(self)-> list[provider_runtime_configuration.JsonObject]:...

    def get_property(self,key:str, default:object=None)->object:...

class TurnExecutionApplication(typing.Protocol):

    agent_instances: dict[str, agent_instance_module.AgentInstance]
    persistence_plane: persistence_plane_module.PersistencePlane | None

class TurnExecutionApplication(TurnExecutionApplication,typing.Protocol):

    current_default_agent_id: str|None
    app_mode:str
    config_manager: ModelProfileConfigManager
    agent_factory: agent_factory_module.AgentFactory|None
    agent_tree: agent_tree_module.AgentTree
    hook_lifecycle: hook_lifecycle_module.HookLifecycleCoordinator|None
    user_project_home:str|None

    def build_agent_for_invoke(self,agent_id:str) -> react_agent_module.ReActAgent:...

    async def request_pending_hook_rewake(self, session_id: str)->bool:...

class TurnExecutionOwner(typing.Protocol):

    async def _run_turn_completion_hooks(
        self,
        entry_agent: react_agent_module.ReActAgent,
        response: message_module.Msg,
        session_id: str,
        *,
        model_index: int|None,
    )->message_module.Msg:...