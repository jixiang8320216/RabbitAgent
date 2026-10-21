import asyncio
import copy

import pydantic
import logging


import domains.configuration.domain.model.app_config as application_configuration
import domains.configuration.infrastructure.adapter.config_manager as configuration
import domains.observability.domain.rabbit_observer as base_observer_module
import domains.persistence.application.persistence_plane as persistence_plane_module
import domains.persistence.domain.store_ports as persistence_ports
import domains.agent.application.lifecycle.id_generator as agent_id_generator_module
import domains.mcp.infrastructure.client_runtime as client_runtime
import domains.agent.application.scheduling.agent_tree as agent_tree_module
import domains.agent.application as groupchat_application
import domains.agent.application.lifecycle.session_resume_service as session_resume_service
import domains.agent.infrastructure.file_agent_registry as file_agent_registry
import domains.agent.domain.entities.role_context as role_context_module
import domains.memory.application.memory_runtime_service as memory_runtime_service
import domains.persistence.infrastructure.file.file_persisternce_factory as file_persisternce_factory
import domains.agent.domain.value_objects.runtime_context as process_runtime_context
import domains.persistence.infrastructure.file.file_session_snapshot_store as file_session_snapshot_store
import domains.hook.domain.resource_models as hook_resource_models
import domains.hook.application.hook_manager as legacy_hook_manager
import domains.plugin.application.plugin_manager as plugin_runtime
import domains.agent.application.registry.model_registry as model_registry_module
import domains.agent.application.prompt.builtin_mode_declarations as builtin_mode_declarations
import domains.configuration.domain.runtime_state.execution_context as execution_context_module
import domains.session.infrastructure.session_persistence  as session_persistence_module
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.domain.entities.agent_instance as agent_instance
import domains.agent.domain.entities.agent_runtime_spec as agent_runtime_spec_module
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.plugin.domain as plugin_contracts
import domains.agent.domain.entities.agent_mcp_registration as agent_mcp_registration
import domains.configuration.domain.runtime_state.session_snapshot as session_snapshot
import domains.plan.application.plan_runtime as plan_runtime_module
import domains.agent.application.loader.agent_factory as agent_factory_module
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.agent.application.scheduling.scheduler as agent_scheduler_module
import domains.runtime.application.participant_registry as participant_registry_module
import domains.observability.application.observer_composition as composite_observer
import domains.agent.infrastructure.file_agent_execution_store as file_agent_execution_store
import domains.agent.infrastructure.file_agent_spawn_mainfest_repository as file_agent_spawn_mainfest_repository
import domains.agent.application.context.message_queue as agent_message_queue
import domains.agent.application.lifecycle.recovery_service as agent_recovery_service
import domains.configuration.infrastructure.adapter.hook_runtime_adapters as  hook_runtime_adapters
import domains.runtime.application.hook_lifecycle as hook_lifecycle_module
import domains.runtime.application.hook_management as hook_management
import domains.runtime.application.runtime_model_config as runtime_model_config
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
import domains.plugin.application.request_context as context_module
import domains.runtime.application.plugin_hook_bridge as plugin_hook_bridge
import domains.agent.domain.value_objects.conversation_context as context_module_2
import pathlib
import domains.tool.lsp as lsp_module
import typing

import domains.permission as permission
import domains.permission as confirmation
import domains.hook as hook
import domains.persistence as persistence
from domains.agent.domain.entities import agent_runtime_spec
from domains.observability.infrastructure.observers import noop_observer

logger = logging.getLogger(__name__)


class _AgentContextsView:

    __slots__ = ("_app",)

    def __init__(self, app:RabbitApplicationCompositionState)->None:
        self._app = app

    def __getitem__(self, agent_id:str)->context_module_2.Context:
        inst=self._app.agent_instances[agent_id]
        return inst.context

    def __setitem__(self, agent_id:str, agent_context_state:context_module_2.Context)->None:
        inst=self._app.agent_instances.get(agent_id)
        if inst is None:
            inst= _compat_placeholder_agent_instance(
                agent_id,
                role="entry",
                context=agent_context_state,
            )
            self._app.agent_instances[agent_id] = inst
        else:
            inst.context = agent_context_state

class _AgentConfigsView:

    __slots__ = ("_app",)

    def __init__(self, app:RabbitApplicationCompositionState)->None:
        self._app = app

    def __getitem__(self, agent_id:str)->agent_runtime_module.AgentRuntimeSpec:
        return self._app.agent_instances[agent_id].config

    def __setitem__(self, agent_id:str, cfg)->None:
        inst=self._app.agent_instances.get(agent_id)
        if inst is None:
            inst= agent_instance.AgentInstance(
                agent_id=agent_id,
               spec=cfg,
                context=context_module_2.Context(),
            )
            self._app.agent_instances[agent_id] = inst
        else:
            inst.spec = cfg


class _RoleContextByAgentView:

    __slots__ = ("_app",)

    def __init__(self, app:RabbitApplicationCompositionState)->None:
        self._app = app

class _SubAgentInstancesView:

    __slots__ = ("_app",)

    def __init__(self, app:RabbitApplicationCompositionState)->None:
        self._app = app

    def _is_default(self, agent_id:str)->bool:
        return agent_id ==self._app.current_default_agent_id

    def __getitem__(self, agent_id:str)->agent_module.ReActAgent:
        if self._is_default(agent_id):
            raise KeyError(agent_id)
        return self._app.agent_runtimes[agent_id]

    def get(self, agent_id:str, default:None=None)->agent_module.ReActAgent|None:
        if self._is_default(agent_id):
            raise default
        return self._app.agent_runtimes.get(agent_id, default)

    def __contains__(self, agent_id:object)->bool:
        return isinstance(agent_id,str) and not self._is_default(agent_id) and agent_id in self._app.agent_runtimes

    def _setitem(self, agent_id:str, runtime:agent_module.ReActAgent) -> None:
        self._app.register_agent_runtimes(agent_id,runtime)

    def __delitem__(self, agent_id:str):
        del self._app.register_agent_runtimes[agent_id]

    def __iter__(self):
        return (aid for aid in self._app.agent_runtimes if not self._is_default(aid))

    def keys(self):
       return  (aid for aid in self._app.agent_runtimes if not self._is_default(aid))

    def values(self):
        return (runtime for aid,runtime in self._app.agent_runtimes.items() if not self._is_default(aid))

    def items(self):
        return ((aid,runtime) for aid,runtime in self._app.agent_runtimes.items() if not self._is_default(aid))

    def __len__(self)->int:
        return sum(1 for aid in self._app.agent_runtimes if not self._is_default(aid))
    def __eq__(self, other)->bool:
        if isinstance(other, dict):
            return dict(self.items()) == other
        return NotImplemented

    def clear(self)->None:
        default_id = self._app.current_default_agent_id
        default_runtime = self._app.agent_runtimes.get(default_id) if default_id else None
        self._app.agent_runtimes.clear()
        if default_id and default_runtime is not None:
            self._app.agent_runtimes[default_id] = default_runtime

class _SubAgentConfigsMapView:

    __slots__ = ("_app",)

    def __init__(self, app:RabbitApplicationCompositionState)->None:
        self._app = app

    def __getitem__(self, name:str):
        return self._app.skill_catalog[name]

    def get(self, name:str, default=None):
        return self._app.skill_catalog.get(name,default)

    def __contains__(self, name:str)->bool:
        return name in self._app.skill_catalog

    def _setitem(self, name:str, config) -> None:
        self._app.skill_catalog[name] = config

    def __delitem__(self, name:str):
        del self._app.skill_catalog[name]

    def __iter__(self):
        return iter(self._app.skill_catalog)

    def keys(self):
       return self._app.skill_catalog.keys()

    def values(self):
        return self._app.skill_catalog.values()

    def items(self):
        return self._app.skill_catalog.items()

    def __len__(self)->int:
        return len(self._app.skill_catalog)

    def clear(self)->None:
        self._app.skill_catalog.clear()
# ---------------------------------------------------------------------------
# 兼容占位实例
# ---------------------------------------------------------------------------

#: 占位实例在 ``spec.extra`` 上打的标记。
#:
#: ``rabbit_application.register_agent_runtime`` 与 ``bind_agent_identity`` 都靠
#: 这个标记把占位实例挡在门外——占位不是真 agent，不能被登记成运行时，也不能
#: 被当成已绑定的身份（两者都会抛 ``agent_id is unkown``）。
COMPAT_PLACEHOLDER_SPEC_FLAG = "compatibility_placeholder"


class _CompatPlaceholderAgentSpec:
    """``agent.model.agent_runtime_spec.AgentRuntimeSpec`` 不可用时的兜底 spec。

    只实现消费点真正会读的两个属性：``agent_id`` 与 ``extra``。这样即便
    ``AgentRuntimeSpec`` 尚未落地（模块为空），``contexts[agent_id] = ctx``
    这条兼容路径也不会因为缺 spec 而崩。
    """

    __slots__ = ("agent_id", "extra")

    def __init__(
        self,
        agent_id: str,
        extra: typing.Optional[typing.Dict[str, pydantic.JsonValue]] = None,
    ) -> None:
        self.agent_id = agent_id
        self.extra: typing.Dict[str, pydantic.JsonValue] = dict(extra or {})
        self.extra.setdefault(COMPAT_PLACEHOLDER_SPEC_FLAG, True)

    def __repr__(self) -> str:
        return f"_CompatPlaceholderAgentSpec(agent_id={self.agent_id!r})"


def _mark_spec_as_placeholder(spec:typing.Any)->None:
    """尽力给 spec 打上占位标记；打不上（只读/类型不符）时只记日志。"""
    try:
        extra = getattr(spec, "extra", None)
        setter = getattr(extra, "setdefault", None)
        if callable(setter):
            setter(COMPAT_PLACEHOLDER_SPEC_FLAG, True)
        elif extra is None:
            logger.debug("agent spec %r has no 'extra' mapping; placeholder flag not set", spec)
    except Exception as exc:
        logger.debug("failed to mark agent spec as compatibility placeholder: %s", exc)


def _compat_placeholder_agent_spec(
    agent_id: str,
    *,
    role: str = "entry",
)->typing.Any:
    """构造占位 spec：优先用真实的 ``AgentRuntimeSpec``，拿不到再退到本地兜底类。

    ``AgentRuntimeSpec`` 的字段契约目前未定型（模块仍在施工），所以这里不硬编码
    构造参数，而是「先按 ``agent_id`` / ``extra`` 两个关键字试，失败再降级」，
    保证任何一版 spec 落地后都不需要改本文件。
    """
    extra: typing.Dict[str, pydantic.JsonValue] = {COMPAT_PLACEHOLDER_SPEC_FLAG: True}
    if role:
        extra["role"] = role

    spec_cls = getattr(agent_runtime_spec_module, "AgentRuntimeSpec", None)
    if spec_cls is not None:
        builders = (
            lambda: spec_cls(agent_id=agent_id, extra=extra),
            lambda: spec_cls.model_construct(agent_id=agent_id, extra=extra),
        )
        for builder in builders:
            try:
                spec = builder()
            except Exception as exc:
                logger.debug("placeholder agent spec construction failed, degrading: %s", exc)
                continue
            _mark_spec_as_placeholder(spec)
            return spec
        logger.debug(
            "AgentRuntimeSpec is present but not constructible for %s; using local placeholder spec",
            agent_id,
        )
    return _CompatPlaceholderAgentSpec(agent_id, extra)


def _compat_placeholder_agent_instance(
    agent_id: str,
    *,
    role: str,
    context: context_module_2.Context|None=None,
    role_context_dict: agent_runtime_spec.JsonObject|None=None,
)->agent_instance.AgentInstance:
    spec= session_snapshot.AgetnConfig(
        agent_id=agent_id,
        role=role,
        name=agent_id,
        extra={
            "compatibility_placeholder": True,
            "compatility_placeholder_version":1,
            "placeholder_source": "legacy_agent_surface",
            "placeholder_created_by":"RabbitApplication.compatibility_view",
            "removal_trigger":"delete_after_legacy_app_memory_and_sub_agent_instance_writes_are_zero",
        },
    )
    return agent_instance.AgentInstance(
        agent_id=agent_id,
        spec=spec,
        context=context if context is not None else context_module_2.Context(),
        role_context_dict=role_context_dict if role_context_dict is not None else {},
    )


class _SessionDirectoryResolver:
    """会话目录解析器。

    把 ``RuntimeContext.sessions_dir`` 作为根目录，按 ``session_id`` 推导出该
    会话自己的目录。纯路径计算，不创建目录、不校验存在性，因此可以安全地在
    构造阶段使用。
    """

    __slots__ = ("_base_dir",)

    def __init__(self, base_dir: pathlib.Path) -> None:
        self._base_dir = base_dir

    @property
    def base_dir(self) -> pathlib.Path:
        """所有会话目录的根目录。"""
        return self._base_dir

    def session_dir(self, session_id: str) -> pathlib.Path:
        """返回指定会话的目录（不保证已存在）。"""
        return self._base_dir / str(session_id)

    def __repr__(self) -> str:
        return f"_SessionDirectoryResolver(base_dir={str(self._base_dir)!r})"


class RabbitApplicationCompositionState:


    def __init__(
        self,
        config: application_configuration.AppConfig,
        observer: typing.Optional[base_observer_module.RabbitObserver]=None,
        *,
        persistence_plane: typing.Optional[persistence_plane_module.PersistencePlane]=None,
        session_snapshot_store: persistence_ports.SessionSnapshotStore | None=None,
        permission_resolver:persistence.PermissionResolver|None=None,
        agent_id_generator: agent_id_generator_module.AgentIdGenerator|None=None,
        )->None:

        self.config = config
        self.runtime_config_snapshot=copy.deepcopy(config.runtime_config_snapshot or {})
        self.config_manager = self.__create_config_manager(config)
        self.mcp_manager: client_runtime.MCPManager|None=None

        self.agent_tree = agent_tree_module.AgentTree()

        self._chat_board: groupchat_application.GroupChatBoard | None = None

        self.app_getter_for_resume: session_resume_service.SessionResumeApplicationGetter |None = None

        self._hook_resume_service = session_resume_service.SessionResumeService(
            registry_loader=file_agent_registry.load_agent_registry,
        )
        self._hook_resume_service.set_app_getter(
            typing.cast(
                session_resume_service.SessionResumeApplicationGetter,
                lambda _session_id:self,
            ))

        self.app_mode = config.app_mode
        self.session_type:str |None=None

        self.role_context = role_context_module.RoleContext()
        self._base_system_prompt: typing.Optional[str]=None
        self.llm_config= config.llm_config
        self.sub_agents_dir= config.sub_agents_dir
        self.root_agent_prompts_dir = config.root_agent_prompts_dir
        self.project_system_prompt = config.project_system_prompt
        self.user_project_home = config.user_project_home
        # ``PluginOrchestrationHost`` 协议声明了 ``user_project_name``，
        # 而 ``AppConfig`` 只有 ``user_project_home``（项目根目录）。这里补一个
        # 同源属性：优先用配置里显式给出的项目名，缺省时用项目根目录名。
        # 原先两边都没有这个属性，插件编排层访问时会抛
        # "'RabbitApplication' object has no attribute 'user_project_name'"。
        self.user_project_name: pathlib.Path = pathlib.Path(
            config.user_project_home
            if config.user_project_home
            else pathlib.Path.cwd()
        ).name
        self.memory_runtime=(
            memory_runtime_service.MemoryRuntimeService(config.memory_settings)
            if config.memory_settings is not None
            else None
        )
        if persistence_plane is not None:
            self.persistence_plane = persistence_plane
        else:
            self.persistence_plane = file_persisternce_factory.create_file_persisternce_plane()

        runtime_context = process_runtime_context.get_runtime_context()
        sessions_dir = runtime_context.sessions_dir
        if sessions_dir is None:
            raise RuntimeError("RuntimeContext.sessions_dir is required to initiallize RabbitApplication")
        self.session_snapshot_store = (
            session_snapshot_store
            if session_snapshot_store is not None
            else file_session_snapshot_store.FileSessionSnapshotStore(sessions_dir)
        )

        self.permission_resolver = (
            permission_resolver if permission_resolver is not None else permission.AllowAllPermissionResolver()
        )

        self.agent_id_generator = (
            agent_id_generator
            if agent_id_generator is not None
            else agent_id_generator_module.DefaultAgentIdGenerator()
        )

        self.session_manager= self._create_session_manager(sessions_dir)
        if config.system_resources is not None:

            self.hook_snapshot = config.system_resources.hooks_config
            self.hook_policy = config.system_resources.hook_policy
        else:
            self.hook_snapshot = hook.HookSnapshot.empty()
            self.hook_policy = hook.HookPolicy()
        self.hook_manager = legacy_hook_manager.HookManager()
        self._hook_component_lock =asyncio.Lock()
        self._active_hook_agents:dict[
            str,
            tuple[hook_resource_models.HookAgentCompomnentSource,int],
        ]={}

        self._hook_agent_source_overrides:dict[str,str]={}
        self.plugin_manager = plugin_runtime.PluginManager()
        self.mode_registry = model_registry_module.ModelRegistry()
        self.active_mode_declaration: plugin_contracts.ModeDeclaration |None=None
        self.agent_mcp_registration_report : agent_mcp_registration.AgentMcpRegistrationReport |None=None

        #注册内置模式
        builtin_modes = builtin_mode_declarations.get_builtin_mode_definitions()
        for _mode_name, mode_def in builtin_modes.items():
            self.mode_registry.register(mode_def,source="builtin")
        self.session=None
        self.new_session_manager=None
        self.execution_context:execution_context_module.ExecutionContext|None=None
        self.session_persistence: session_persistence_module.SessionPersistence|None=None
        self.agent_instances:typing.Dict[str, agent_instance.AgentInstance] = {}
        self._durable_app_id: str | None = None
        self._agent_instance_load_locks: dict[str, asyncio.Lock]={}
        self._agent_instance_write_locks: dict[str, asyncio.Lock]={}

        self.agent_runtimes: typing.Dict[str,agent_module.ReActAgent]={}
        self._compress_in_flight_agent_keys: set[tuple[str,str]] = set()
        self._compress_in_flight_guard_lock: asyncio.Lock=asyncio.Lock()
        self._compress_tasks_by_run_id: dict[str, asyncio.Task[object]]={}
        self._compression_runs_by_agent:dict[str, dict[str, pydantic.JsonValue]]={}
        self.skill_catalog:dict[str, agent_runtime_module.AgentRuntime]={}
        self.contexts = _AgentContextsView(self)
        self.agent_configs = _AgentConfigsView(self)
        self.role_context_by_agent = _RoleContextByAgentView(self)
        self.sub_agent_instances = _SubAgentInstancesView(self)
        self.sub_agent_configs_map =_SubAgentConfigsMapView(self)


        self.default_agent_history:typing.Dict[str,typing.List[session_snapshot.DefaultRecored]]={}
        self.current_default_agent_id:typing.Optional[str]=None

        self.plan_runtime:plan_runtime_module.PlanRuntime|None=None

        self._current_invoke_agent: typing.Optional[agent_module.ReActAgent]=None
        self.delegate_agent:agent_module.ReActAgent|None=None

        self.agent_factory: agent_factory_module.AgentFactory|None=None
        self.root_agent_config: root_agent_config_module.RootAgentConfig|None=None
        self.sub_agents:list[agent_runtime_module.AgentRuntime]=[]
        # 按名字索引的子 agent 视图，供 MCP 侧按名取用。
        # 与 ``sub_agents`` 内容一致但结构不同：这里是 ``{name: runtime}``，
        # 由 ``agent_bootstrap`` 在每次装配时全量重建（``clear()`` 后重新填充）。
        self.sub_agent_configs_mcp:dict[str, agent_runtime_module.AgentRuntime]={}
        self.agent_lifecycle:None=None
        self._default_agent_id_override:str |None=None
        self._agent_scheduler: agent_scheduler_module.AgentScheduler|None=None
        self.client_id: typing.Optional[str]=None
        self.connection_manager=None
        self._lsp_tools_instance: lsp_module.LSPTools |None=None
        self._lsp_root_paths:str |None=None
        self._lsp_language:str |None=None

        self.participant_registry= participant_registry_module.ParticipantRegistry()

        self.topic:typing.Optional[str]=None
        self.tags: typing.Dict[str,str]={}
        self.category_id:typing.Optional[str]=None
        self.worktree_name: typing.Optional[str]=None
        self.worktree_path:typing.Optional[str]=None

        self.external_ai_sessions:typing.Dict[str,typing.Dict[str,str]]={}

        base_observer= observer if observer is not None else  noop_observer.NoOpObserver()
        composite_observers = [base_observer]

        lifecycle_observer = _resolve_lifecycle_observer()
        if lifecycle_observer is not None:
            composite_observers.append(lifecycle_observer)
        self.observer = composite_observer.CompositeRabbitObserver(composite_observers)

        recovery_execution_store = file_agent_execution_store.create_file_agent_execution_store()
        recovery_manifest_repository= file_agent_spawn_mainfest_repository.FileAgentSpawnMainfestRepository(
            recovery_execution_store
        )

        message_queue= agent_message_queue.AgentMessageQueue.get_instance()
        self.agent_recovery_service= agent_recovery_service.AgentRecoveryService(
            registry_loader=file_agent_registry.load_agent_registry,
            execution_store =recovery_execution_store,
            manifest_repository=recovery_manifest_repository,
            message_queue =message_queue,
        )

        self.hook_lifecycle: hook_lifecycle_module.HookLifecycleCoordinator|None=None
        self.hook_management: hook_management.HookManagermentUseCases |None=None
        hook_state_store= self.persistence_plane.hook_state
        hook_run_store = self.persistence_plane.hook_run
        session_event_store = self.persistence_plane.session_event
        project_dir_value = self.user_project_home or runtime_context.default_project_home
        apps_dir = runtime_context.apps_dir or runtime_context.sessions_dir

        if(
            hook_state_store is not None
            and hook_run_store is not None
            and session_event_store is not None
            and project_dir_value is not None
            and apps_dir is not None
        ):
            mcp_hook_client = hook_runtime_adapters.HookMcpClientAdapter(lambda: self.mcp_manager)
            model_hook_client = hook_runtime_adapters.HookModelClientAdapter(
                self.config_manager,
                self.llm_config,
                global_proxy_configuration= runtime_model_config.read_global_proxy_configuration(
                    self.config_manager,
                ),
            )

            self.hook_lifecycle = hook_lifecycle_module.HookLifecycleCoordinator(
                self.hook_snapshot,
                self.hook_policy,
                project_dir=pathlib.Path(project_dir_value),
                rabbit_data_dir=runtime_context.user_data_home,
                apps_dir=apps_dir,
                state_store=hook_state_store,
                run_store=hook_run_store,
                event_store=session_event_store,
                observer=self.observer,
                mcp_client=mcp_hook_client,
                model_client=model_hook_client,
                configuration_paths=(
                    config.system_resources.hook_configuration_paths if config.system_resources is not None else ()
                ),
                configuration_fingerprint=(
                    config.system_resources.hook_configuration_fingerprint
                    if config.system_resources is not None
                    else None
                ),
                initial_diagnostics=(
                    config.system_resources.hook_diagnostics if config.system_resources is not None else ()
                ),
                configuration_refresher=(
                    config.system_resources.hook_configuration_refresher
                    if config.system_resources is not None
                    else None
                ),
                component_context_updater=(
                    config.system_resources.hook_conponent_context_updater
                    if config.system_resources is not None
                    else None
                ),
                file_watcher=(
                    config.system_resources.hook_file_watcher if config.system_resources is not None else None
                ),
                async_rewake_scheduler=self,
            )
            configuration_store=(
                config.system_resources.hook_configuration_store if config.system_resources is not None else None
            )
            if configuration_store is not None:
                self.hook_management = hook_management.HookManagementUseCases(
                    self.hook_lifecycle.snapshots,
                    self.hook_lifecycle.runtime,
                    self.hook_lifecycle.state,
                    hook_run_store,
                    configuration_store,
                    hook.HookRuntimeDirectories(
                        project_dir=str(pathlib.Path(project_dir_value).resolve()),
                        rabbit_data_dir=str(runtime_context.user_data_home.resolve()),
                    )
                )
        else:
            logger.warning(
                "configuration Hook runtime is unavailable; hook_management is not created"
            )

        runtime_bindings.set_message_queue_getter(agent_message_queue.AgentMessageQueue.get_instance)

        class  _InterruptCheckAdapter:
            def check_interrupt(self, session_id:str)->bool:
                return confirmation.InterruptManager.check_interrupt(session_id)

            def get_turn_scope(self, session_id:str):
                return confirmation.InterruptManager.get_turn_scope(session_id)
        runtime_bindings.set_interrupt_checker(_InterruptCheckAdapter())

        runtime_bindings.set_plugin_hook_bridge(
            plugin_hook_bridge.PluginHookBridgeAdapter(context_module.get_plugin_manager)
        )

        logger.info(f"[RABBIT APP] App mode {self.app_mode}")

    @property
    def lsp_tools(self)->lsp_module.LSPTools|None:
        """LSP 工具实例（``AgentToolkitBuilder`` 按这个名字取用）。

        存储字段是 ``_lsp_tools_instance``，对外统一走这个 property：
        一处改名不用改所有调用点，也避免「属性存在但值是旧实例」这类偏差。

        注意：property 必须定义在 ``__init__`` 之外。放在 ``__init__`` 体里
        会把后面所有初始化语句吞进 setter（缩进同为 8 空格），导致
        ``self.observer`` 等一整批属性静默不初始化。
        """
        return self._lsp_tools_instance

    @lsp_tools.setter
    def lsp_tools(self, value: lsp_module.LSPTools|None)->None:
        self._lsp_tools_instance = value

    def __create_config_manager(self,
        config:application_configuration.AppConfig
    )-> configuration.ConfigManager:
        return configuration.ConfigManager(
            config.config_file,
            config_overrides=config.config_overrides,
        )

    def _create_session_manager(self, sessions_dir: str | pathlib.Path) -> typing.Any:
        """创建会话管理器。

        会话的持久化目录由 ``RuntimeContext.sessions_dir`` 提供，本方法只负责
        把该目录包装成一个「按 session_id 定位会话工作区」的轻量视图，
        不在这里做任何磁盘 IO——真正的落盘由 ``session_snapshot_store`` 与
        ``self.persistence_plane`` 承担。

        之所以返回一个独立的小对象而不是直接返回 ``pathlib.Path``，是为了让
        后续调用点（``session_orchestrator`` 等）可以通过属性访问会话目录，
        而不必四处重复拼接路径。
        """
        base_dir = pathlib.Path(sessions_dir).expanduser().resolve(strict=False)
        return _SessionDirectoryResolver(base_dir)

def _resolve_lifecycle_observer()->base_observer_module.RabbitObserver|None:
    notifier = runtime_bindings.get_lifecycle_notifier()
    if notifier is None:
        return None
    # ``observer`` 必须先初始化为 None：notifier 可能不提供 lifecycle_observer
    # （旧版 notifier、或生命周期能力未安装），此时 except 分支只记日志，
    # 若直接落到下面的 isinstance 判断会抛 UnboundLocalError。
    observer: object | None = None
    try:
        observer = notifier.lifecycle_observer()
    except Exception as e:
        logger.warning("failed to resolve lifecycle observer: %s", e)

    if not isinstance(observer, base_observer_module.RabbitObserver):
        logger.error("[RABBIT APP] lifecycle_observer is unavailable")
        return None
    return observer
