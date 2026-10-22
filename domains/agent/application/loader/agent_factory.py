import inspect
import typing
import logging

import pydantic

import domains.agent.application.lifecycle.runtime_composition as agent_runtime_composition
import domains.agent.domain.aggregates.react_agent as runtime_react_agent
from domains.agent.infrastructure import file_agent_spawn_mainfest_repository, file_agent_registry
import domains.agent.application.context.context_optimizer_adapter as agent_context_adapter
import domains.agent.application.toolkit.toolkit_builder as agent_toolkit_builder
import domains.agent.application.context.execution_store as agent_execution_store
import domains.agent.application.scheduling.scheduler as agent_scheduler
import domains.agent.application.lifecycle.session_resume_service as session_resume_service
from domains.configuration.infrastructure.adapter import config_manager as configuration
import domains.agent.domain.value_objects.message as message
from domains.observability.infrastructure.observers import noop_observer
import domains.agent.application.lifecycle.instance_manager as instance_lifecycle
import domains.llm.provider.composition as provider_composition
import domains.agent.application.prompt.system_prompt_builder as prompt_builder
import domains.llm.provider.application.model.adapter_factory as model_acces_service
import domains.agent.application.context.model_context_factory as model_context_factory
import domains.plan.application.plan_hooks as plan_hooks_module
import domains.agent.infrastructure.file_agent_execution_store as file_agent_execution_store
import domains.skill.service.skill_registry as skill_registry_module
import domains.configuration.domain.runtime_state.system_resources as system_resource_module
import domains.mcp.infrastructure.client_runtime as client_runtime
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.application.hook.runtime_hook_builder as hook_builder_module
import domains.observability.domain.rabbit_observer as base_observer_module
import domains.agent.domain.aggregates.message_formatter as message_formatter
import domains.agent.application.context.spawn_manifest_repository as agent_spawn_mainfest_repository
import domains.agent.domain.entities.agent_role as agent_role_module
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.agent.domain.entities.agent_context as agent_context_module
import domains.plugin.application.request_context as plugin_context
import domains.agent.domain.entities.agent_runtime_spec as agent_runtime_spec_module
# ``Toolkit`` 的真身在 ``tool.core.application.toolkit``（与 agent_toolkit_builder
# 的导入一致）；此前 import 的是没有 ``Toolkit`` 的 ``toolkit_assembly``，导致
# ``register_plugin_tools`` 的注解在 import 期就 AttributeError。
import domains.tool.core.application.toolkit as toolkit_module
import domains.plugin.application.plugin_contracts as plugin_contracts
import domains.agent.domain.value_objects.tool_response as tool_response
import domains.plugin.application.tool_execution as tool_interceptor
logger = logging.getLogger(__name__)


def _optional(source: object, *names: str, default: object = None) -> object:
    """从对象或映射上取一个可选字段，取不到返回 ``default``。"""
    if source is None:
        return default
    for name in names:
        if isinstance(source, dict):
            if name in source:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return default


def _optional_str(source: object, *names: str) -> str | None:
    value = _optional(source, *names)
    if isinstance(value, str) and value:
        return value
    if value is None or isinstance(value, str):
        return None
    text = str(value)
    return text or None


def _as_int(value: object, default: int = 0) -> int:
    """严格转 int；转不了（None / 非数字 / 负数）用默认值。"""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value if value >= 0 else default
    if isinstance(value, float):
        return int(value) if value >= 0 else default
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return default
        return parsed if parsed >= 0 else default
    return default


def _as_str_list(value: object) -> list[str]:
    """转成 ``list[str]``（``AgentRuntimeSpec`` 是 strict 模型，元组/None 都不收）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


class AgentFactoryApplicationPort(
    agent_runtime_composition.AgentRuntimeApplicationPort,
    typing.Protocol,
):
    app_mode : str

class AgentFactory:
    """agent 工厂。

    把「创建 agent 所需的全部环境依赖」一次性收齐并持有，后续创建各模式
    agent 时就不再需要到处找这些依赖：

    - ``mcp_manager``：MCP 运行时，agent 的外部工具来源。
    - ``config_manager``：配置管理器。
    - ``app_instance``：宿主应用（``AgentFactoryApplicationPort``）。
    - ``skill_registry`` / ``skill_registry_refresher``：技能注册表及其刷新器。
    - ``rabbit_skill_md``：聚合后的技能说明 markdown。
    - ``instruction_resources``：指令资源（沿用调用点拼写）。

    装配位置：``AgentBootstrapService.execute``。依赖全部可选——缺哪一项都只
    是对应的能力不可用，不应让工厂本身构造失败。
    """

    def __init__(
        self,
        *,
        mcp_manager: client_runtime.MCPManager ,
        config_manager: configuration.ConfigManager ,
        app_instance: AgentFactoryApplicationPort | None = None,
        skill_registry:typing.Optional[skill_registry_module.SkillRegistry] = None,
        skill_registry_refresher: typing.Optional[skill_registry_module.SkillRegistryRefresher] = None,
        rabbit_skills_md:typing.Optional[str] = None,
        instruction_resources: typing.Sequence[system_resource_module.InstructionResource] = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.config_manager = config_manager
        self.app_instance = app_instance
        self.skill_registry = skill_registry
        self.skill_registry_refresher = skill_registry_refresher
        self.rabbit_skills_md = rabbit_skills_md
        self.instruction_resources = instruction_resources
        self._execution_store = file_agent_execution_store.create_file_agent_execution_store()
        # 属性名与 ``manifest_repository`` property（以及 ``agent_recovery_service``）
        # 保持一致：此前写成 ``_mainfest_repository``，property 读的是
        # ``_manifest_repository``，一调用就 AttributeError。
        self._manifest_repository= file_agent_spawn_mainfest_repository.FileAgentSpawnMainfestRepository(
            self._execution_store
        )

        if app_instance is not None:
            self.observer = app_instance.observer
        else:
            self.observer = noop_observer.NoOpObserver()

        self.plan_hooks = plan_hooks_module.PlanHookManager(
            observer=self.observer,
        )

        self._current_user_request=""
        app_llm_config= self.app_instance.llm_config if self.app_instance  is not None else None
        optimizer_bootstrap = agent_context_adapter.ContextOptimizerBootstrapper.bootstrap(
            logger,
            app_llm_config = app_llm_config,
        )
        self.context_optimizer = optimizer_bootstrap.optimizer if optimizer_bootstrap is not None else None
        self._system_prompt_builder = prompt_builder.SystemPromptBuilder(
            config_manager=self.config_manager,
            context_optimizer=self.context_optimizer,
            current_user_request=self._current_user_request,
            skill_registry=self.skill_registry,
            rabbit_skills_md=self.rabbit_skills_md,
            # ``SystemPromptBuilder`` 的形参沿用既有拼写 ``instrction_resources``，
            # 本类对外暴露的构造参数是正确拼写 ``instruction_resources``，
            # 因此这里在边界上做一次名字对齐（只此一处）。
            instruction_resources=self.instruction_resources,
        )

        logger.debug("initialized SystemPromptBuilder for prompt construction")

        self._model_context_factory = model_context_factory.ModelContextFactory(
            configmanager=self.config_manager,
            token_counter=None,
        )

        self._token_counter = self._model_context_factory.get_token_counter()

        self.llm_provider = model_acces_service.LLMModelAccessService(
            configuration_source=self.config_manager,
            provider_factory=provider_composition.create_provider_factory(),
        )

        self._agent_toolkit_builder = agent_toolkit_builder.AgentToolkitBuilder(
            mcp_manager=self.mcp_manager,
            app_instance=self.app_instance,
            skill_registry=self.skill_registry,
            skill_registry_refresher=self.skill_registry_refresher,
        )

        self._agent_instance_manager= instance_lifecycle.AgentInstanceManager()

    @property
    def token_counter(self)->provider_composition.TokenCounter:
        return self._token_counter

    def create_formatter(self,
        llm_config: agent_runtime_module.AgentRuntimeModelConfig
        )-> message_formatter.MessageFormatter:
        return self._model_context_factory.create_formatter(llm_config)

    @property
    def execution_store(self)->agent_execution_store.AgentExecutionStore:
        return self._execution_store

    def manifest_repository(self)->agent_spawn_mainfest_repository.AgentSpawnManifestRepository:
        return self._manifest_repository


    async def create_react_agent(self,
         sub_agent: agent_runtime_module.AgentRuntime,
         override_name: str|None = None,
         hook_builder: hook_builder_module.AgentRuntimeHookBuilder|None = None,
         observer: base_observer_module.RabbitObserver|None = None,
         on_progress: agent_toolkit_builder.AgentToolkitProgressCallback|None = None,
    )->runtime_react_agent.ReActAgent:
        composition_host= typing.cast(agent_runtime_composition.AgentRuntimeCompositionHost,self)
        return await agent_runtime_composition.create_react_agent(
            composition_host,
            sub_agent,
            react_agent_factory=runtime_react_agent.ReActAgent,
            override_name=override_name,
            hook_builder=hook_builder,
            observer=observer,
            # 组合层的形参是 ``on_process``，工厂对外是 ``on_progress``，
            # 名字在这个边界上对齐一次（调用方无需感知）。
            on_progress=on_progress,
        )

    def resolve_agent_scheduler(self,
        agent_context:agent_context_module.AgentContext|None,
    )->agent_scheduler.AgentScheduler |None:
        if agent_context.registry is None:
            return None
        app=self.app_instance
        if app is not None:
            scheduler=app.agent_scheduler
        else:
            scheduler_candidate= execution_context.get_agent_scheduler()
            scheduler= scheduler_candidate if isinstance(scheduler_candidate, agent_scheduler.AgentScheduler) else None
        if scheduler is not None:
            scheduler.bind_app_instance(app)
            return scheduler

        scheduler= agent_scheduler.AgentScheduler(
            agent_context.session_id,
            agent_context.registry,
            execution_store = self._execution_store,
            mainfest_repository = self.manifest_repository,
        )
        scheduler.bind_app_instance(app)

        resume_service= session_resume_service.SessionResumeService(
            registry_loader=file_agent_registry.load_agent_registry,
        )
        if app is not None and app.app_getter_for_resume is not None:
            resume_service.set_app_getter(app.app_getter_for_resume)
        elif app is not None:
            resume_app = typing.cast(session_resume_service.SessionResumeApplicationPort, app)
            resume_service.set_app_getter(lambda session_id:resume_app if session_id == agent_context.session_id else None)
        else:
            resume_service.set_app_getter(
                lambda _session_id:typing.cast(
                    session_resume_service.SessionResumeApplicationPort|None,
                    execution_context.get_app_instance(),
                )
            )
        scheduler.set_resume_service(resume_service)
        if app is not None:
            app.agent_scheduler=scheduler
        return scheduler


    def materialize_agent_runtime_identity(
        self,
        *,
        agent_id: str,
        sub_agent: agent_runtime_module.AgentRuntime,
        runtime: runtime_react_agent.ReActAgent,
    ) -> None:
        """把运行时本体登记回宿主，使其可被按 id 检索。

        ``ReActAgent`` 上没有 ``model_id`` / ``system_prompt`` 这两个属性
        （分别叫 ``mode_id`` 和 ``_sys_prompt``），所以这里按「优先真名、回落
        等价属性」取，而不是硬取一个不存在的名字——那会在每次装配时抛
        ``AttributeError``。
        """
        app = self.app_instance
        if app is None:
            raise RuntimeError("materialize_agent_runtime_identity: no app instance")
        if runtime.name != agent_id:
            raise ValueError(
                f"materialize_agent_runtime_identity: agent name {runtime.name!r} != {agent_id!r}"
            )

        model_name = (
            getattr(runtime, "model_id", None)
            or getattr(runtime, "mode_id", None)
            or getattr(getattr(runtime, "model", None), "model_name", None)
            or _optional_str(sub_agent, "model_id", "model_name", "model")
        )
        if not model_name:
            raise ValueError(
                f"materialize_agent_runtime_identity: agent {agent_id!r} has no model id"
            )
        system_prompt = (
            getattr(runtime, "system_prompt", None)
            or getattr(runtime, "_sys_prompt", None)
            or _optional_str(sub_agent, "system_prompt")
            or ""
        )

        agent_role = agent_role_module.AgentRole.resolve(
            sub_agent.agent_role,
            sub_agent.agent_slot,
        )
        app.bind_agent_identity(
            agent_id,
            self._build_agent_runtime_spec(
                agent_id=agent_id,
                sub_agent=sub_agent,
                agent_role=agent_role,
                model_name=str(model_name),
                system_prompt=str(system_prompt),
                app_mode=app.app_mode,
                is_root_agent=agent_role.uses_mode_prompt_file,
            ),
            runtime.context,
        )

    def _build_agent_runtime_spec(
        self,
        *,
        agent_id: str,
        sub_agent: agent_runtime_module.AgentRuntime,
        agent_role: agent_role_module.AgentRole,
        model_name: str,
        system_prompt: str,
        app_mode: str | None,
        is_root_agent: bool,
    ) -> agent_runtime_spec_module.AgentRuntimeSpec:
        """把「agent 声明 + 装配结果」收敛成一份 ``AgentRuntimeSpec``。

        ``AgentRuntimeSpec`` 是 ``extra="forbid"`` + ``strict=True`` 的严格模型：
        多一个字段、类型差一点都会整体校验失败。所以这里只投递它声明过的字段，
        并且列表/整数都显式转成它要的类型。
        """
        role_value = getattr(agent_role, "value", None) or str(agent_role)
        extra: dict[str, object] = {
            "is_root_agent": bool(is_root_agent),
            "agent_role": str(role_value),
        }
        if app_mode:
            extra["app_mode"] = str(app_mode)

        return agent_runtime_spec_module.AgentRuntimeSpec(
            agent_id=str(agent_id),
            role=str(role_value),
            name=str(_optional_str(sub_agent, "name", "agent_name") or agent_id),
            description=str(_optional_str(sub_agent, "description") or ""),
            model=str(model_name),
            system_prompt=str(system_prompt),
            max_tokens=_as_int(_optional(sub_agent, "max_tokens"), 0),
            mcp_servers=_as_str_list(_optional(sub_agent, "mcp_servers")),
            skill_refs=_as_str_list(_optional(sub_agent, "skill_refs")),
            plugin_refs=_as_str_list(_optional(sub_agent, "plugin_refs")),
            extra=extra,
        )

    def _hook(self, agent: object) -> None:
        """把 agent 交给应用级 hook manager；没有 hook manager 就什么也不做。

        ``agent_runtime_composition._finalize_agent`` 按这个名字调用。legacy
        hook 体系还没接到工厂上，所以这里做成「能用就用、没有就跳过」，而不是
        让装配因为缺 hook 管理器直接 ``AttributeError``。
        """
        app = self.app_instance
        manager = getattr(app, "hook_manager", None) if app is not None else None
        if manager is None:
            manager = getattr(self, "hook_manager", None)
        if manager is None:
            return
        for name in ("register_agent", "attach_agent", "add_agent", "attach"):
            attach = getattr(manager, name, None)
            if not callable(attach):
                continue
            try:
                attach(agent)
                return
            except TypeError:
                continue
            except Exception:  # noqa: BLE001 - hook 是可选能力
                logger.debug("hook manager %s failed on %r", name, agent, exc_info=True)
                return

    def _register_incremental_save_hook(self, agent: object) -> None:
        """注册「增量保存上下文」钩子（agent 不支持 register_hook 时跳过）。

        保存动作走 ``persist_agent_context_strict``；失败只记日志——增量保存是
        优化手段，丢一次不该打断一轮对话。
        """
        register = getattr(agent, "register_hook", None)
        app = self.app_instance
        if not callable(register) or app is None:
            return
        persist = getattr(app, "persist_agent_context_strict", None)
        if persist is None:
            return

        async def _incremental_save(*args: object, **kwargs: object) -> None:
            context = getattr(agent, "context", None)
            agent_id = getattr(agent, "name", None)
            if context is None or not agent_id:
                return
            try:
                await persist(agent_id, context)
            except Exception:  # noqa: BLE001
                logger.debug("incremental context save failed for %r", agent_id, exc_info=True)

        for event in ("post_reply", "post_iteration", "iteration_end"):
            try:
                register(event, _incremental_save)
                return
            except TypeError:
                continue
            except Exception:  # noqa: BLE001
                logger.debug("register_hook(%s) failed on %r", event, agent, exc_info=True)
                return

    def register_plugin_tools(
        self,
        toolkit: toolkit_module.Toolkit,
        *,
        slot:plugin_contracts.AgentSlot|None,
        agent_name:str,
        allowed_tool_names:set[str]|None=None,
    )->None:

        plugin_manager = plugin_context.get_plugin_manager()
        if plugin_manager is None or not plugin_manager.has_active_plugins():
            return
        include_plugin_tools= slot.include_plugin_tools if slot is not None else True
        if not include_plugin_tools:
            return
        for group_definition in plugin_manager.get_registered_tools_groups():
            try:
                toolkit.create_tool_group(
                    group_name=group_definition.name,
                    description=group_definition.description,
                    active=group_definition.active,
                    notes= group_definition.notes,
                )
            except Exception as e:
                logger.error(f"Failed to create tool group {group_definition.name}: {e}")

        for tool_key, tool_definition in plugin_manager.get_registered_tools(slot=slot).items():
            if not _plugin_tool_allowed(tool_key, tool_definition,allowed_tool_names):
                continue
            if tool_definition.name in toolkit.tools:
                continue
            try:
                group_name=tool_definition.group or "basic"
                _register_plugin_tool_to_toolkit(
                    toolkit,
                    tool_key,
                    tool_definition,
                    group_name=group_name,
                )
            except Exception as e:
                logger.error(f"Failed to register tool {tool_key}: {e}")


# ======================================================================
# 插件工具登记（register_plugin_tools 的两个模块级步骤）
# ======================================================================

def _plugin_tool_active(tool_definition: typing.Any) -> bool:
    """插件工具定义自身的启用开关；``active`` / ``enabled`` 都缺省按启用。"""
    value = _optional(tool_definition, "active", "enabled")
    if value is None:
        return True
    return bool(value)


def _plugin_tool_allowed(
    tool_key: str,
    tool_definition: plugin_contracts.ToolDefinition,
    allowed_tool_names: set[str]|None,
) -> bool:
    if allowed_tool_names is None:
        return True
    candidates={tool_key, tool_definition.name}
    return any(
        agent_toolkit_builder.AgentToolkitBuilder._tool_name_variants(name)& allowed_tool_names
        for name in candidates
        if name
    )


def _register_plugin_tool_to_toolkit(
    toolkit: toolkit_module.Toolkit,
    tool_key: str,
    tool_definition: plugin_contracts.ToolDefinition,
    group_name: str="basic",
) -> None:
    _factory_logger= logging.getLogger(__name__)
    tool_name:str = tool_definition.name
    tool_description:str = tool_definition.description
    tool_parameters = tool_description.parameters
    tool_execute= tool_definition.execute

    json_schema={
        "type": "function",
        "function":{
            "name":tool_name,
            "description":tool_description,
            "parameters":tool_parameters,
        }
    }

    async def _plugin_tool_wrapper(**kwargs: pydantic.Json) -> tool_response.ToolResponse:
        execution_result = tool_execute(kwargs)
        result = await execution_result if inspect.isawaitable(execution_result) else execution_result
        if isinstance(result, tool_response.ToolResponse):
            return result
        text = result if isinstance(result, str) else str(result)
        return tool_response.ToolResponse(content=[message.TextBlock(type="text",text=text)])

    _plugin_tool_wrapper.__name__ = tool_name
    _plugin_tool_wrapper.__doc__ = tool_description
    __intercepted_wrapper =tool_interceptor.wrap_with_plugin_interceptor(_plugin_tool_wrapper, tool_name)
    __intercepted_wrapper.__name__ = tool_name
    __intercepted_wrapper.__doc__ = tool_description
    toolkit.register_tool_function(
        tool_func=__intercepted_wrapper,
        func_name= tool_name,
        json_schema= json_schema,
        group_name= group_name,
        namesake_strategy= "error",
    )


















