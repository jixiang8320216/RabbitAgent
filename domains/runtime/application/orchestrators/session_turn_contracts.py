
import typing
import collections.abc

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


# ── 运行期回调类型别名 ─────────────────────────────────────────────
# 这些回调由宿主在装配时注入；此处给出与调用点一致的别名，避免 import 期
# AttributeError（``session_turn_execution`` 在方法签名里引用它们）。
TurnAdmissionCallback = collections.abc.Callable[..., typing.Any]
ContextPersistenceCallback = collections.abc.Callable[..., typing.Any]
InputOffloadCallback = collections.abc.Callable[..., typing.Any]
PreparedUserMessageCallback = collections.abc.Callable[..., typing.Any]
# 调用点沿用了 ``Perpared`` 拼写，这里同时导出两个名字避免任一处 AttributeError。
PerparedUserMessageCallback = PreparedUserMessageCallback


class TurnContextPersistenceCallbackError(RuntimeError):
    """上下文持久化回调执行失败时抛出（与 ``TurnContextPersistenceIndeterminateError`` 区分）。"""
