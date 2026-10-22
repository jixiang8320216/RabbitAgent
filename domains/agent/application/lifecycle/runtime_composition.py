from __future__ import annotations


from domains.agent.domain.entities import agent_runtime_spec
import domains.agent.application.scheduling.scheduler as agent_scheduler
import domains.agent.application.lifecycle.session_resume_service as session_resume_service
import domains.agent.application.loader.expert_call_service as expert_call_service
import domains.agent.application.toolkit.toolkit_builder as agent_toolkit_builder
import domains.plan.application.plan_runtime as plan_runtime_module
import domains.agent.application.context.context_optimizer_adapter as agent_context_adapter
import domains.memory.application.memory_runtime_service as memory_runtime_service
import typing
import asyncio
import collections.abc
import pydantic
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.agent.application.hook.runtime_hook_builder as hook_builder_module
import domains.observability.domain.rabbit_observer as base_observer_module
import domains.llm.provider.application.context.context_window_resolver as context_window_resolver
import domains.agent.domain.entities.agent_role as agent_role_module
import domains.memory.application.memory_agent_tools as memory_agent_tools
import domains.memory.application.rag_agent_tools as rag_agent_tools
import domains.configuration.infrastructure.merge.rag_config_rules as rag_config_rules
import domains.tool.core.application.toolkit as toolkit_module
import domains.agent.domain.value_objects.runtime_context as process_runtime_context
import domains.plugin.application.request_context as plugin_context
import domains.agent.application.prompt.plugin_prompt_transform as plugin_prompt_transform
import domains.agent.domain.value_objects.conversation_context as runtime_context
import domains.agent.domain.aggregates.execution_context as execution_context

if typing.TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，运行期延迟导入
    # 顶层导入会经 app/__init__ 反向拉起 agent_factory，与本模块形成循环导入。
    import domains.configuration.domain.model.llm_config as llm_config_module

import dataclasses
import datetime
import inspect
import logging

import domains.agent.domain.entities.agent_context as agent_context_module
import domains.agent.domain.value_objects.conversation_context as conversation_context_module
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
try:
    from service import runtime_model_config
except ImportError:  # pragma: no cover - service 包尚未建立，建好后自动生效
    runtime_model_config = None

logger = logging.getLogger(__name__)

class AgentRuntimeCompositionHost(typing.Protocol):


    def resolve_agent_scheduler(self,
        agent_context:agent_context_module.AgentContext|None,
    )->agent_scheduler.AgentScheduler |None:...

    def bind_agent_identity(self
        ,
        agent_id:str,
        agent_spec: agent_runtime_spec.AgentRuntimeSpec,
        agent_context:runtime_context.Context,):...

    def materialize_agent_runtime_identity(self,
        *,
        agent_id:str,
        sub_agent:agent_runtime_module.AgentRuntime,
        runtime:agent_module.ReActAgent,
    )->None:...

class AgentRuntimeApplicationPort(
    agent_toolkit_builder.AgentToolkitApplicationPort,
    agent_scheduler.AgentSchedulerApplicationPort,
    typing.Protocol,
):
    plan_runtime: plan_runtime_module.PlanRuntime |None
    app_getter_for_resume: session_resume_service.SessionResumeApplicationGetter |None
    llm_config: agent_context_adapter.ContextOptimizerConfigurationSource
    memory_runtime: memory_runtime_service.MemoryRuntimeService|None


class AgentRuntimeOptions(pydantic.BaseModel):
    """装配一个 agent 时用到的开关集合。

    取值优先级：sub_agent 声明 > 宿主/应用全局配置 > 内置默认值。

    .. note::
       本类此前被误加了 ``@dataclasses.dataclass(frozen=True)``。dataclass 会用
       自己生成的 ``__init__`` **覆盖** pydantic 的 ``__init__``，而 dataclass 的
       字段语义是「无默认值 = 必填」——于是连 ``enable_meta_tool`` /
       ``mode_id`` / ``model_profile_id`` 这三个**明明有默认值**的字段也变成必填，
       ``_options()`` 只传 6 个参数 → 启动即抛
       ``TypeError: AgentRuntimeOptions.__init__() missing 3 required positional
       arguments: 'enable_meta_tool', 'mode_id', and 'model_profile_id'``。

       pydantic 模型自带构造与校验，不需要 dataclass 装饰，此处移除。
    """

    include_builtin_tools: bool = True
    include_mcp_tools: bool = True
    include_plan_runtime: bool = True
    max_iters: int | None = None
    # 沿用 ``ReActAgent`` 构造参数的历史拼写（parrallel），不要"顺手"改正。
    parallel_tool_calls: bool = False
    enable_meta_tool: bool = False
    mode_id: str | None = None
    model_profile_id: str | None = None
    system_prompt_file: str = ""
    project_home: str | None = None

    @property
    def sytem_prompt_file(self) -> str:
        """历史拼写（缺一个 s）的兼容别名，指向 ``system_prompt_file``。

        注意别名是 ``sytem_prompt_file``（缺一个 s）。此前这里误写成了
        ``system_prompt_file``——与同名字段重名，property 覆盖字段后
        ``self.system_prompt_file`` 会无限递归，读取即 ``RecursionError``。
        """
        return self.system_prompt_file


@dataclasses.dataclass(frozen=True)
class AgentDiagnosticContext:
    """agent 的诊断上下文：随 agent 一生，用于排障与后续补齐 agent_context。"""

    agent_name: str
    created_at: str
    agent_context: typing.Any | None = None
    registry: typing.Any | None = None
    metadata: dict[str, object] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class _FallbackToolkitAssembly:
    """工具集装配缺失时的占位结果，字段与真实装配结果保持一致。"""

    toolkit: typing.Any | None = None
    tool_classification_resolver: typing.Any | None = None


async def create_react_agent(
    host:AgentRuntimeApplicationPort,
    sub_agent:agent_runtime_module.AgentRuntime,
    *,
    react_agent_factory:collections.abc.Callable[...,agent_module.ReActAgent],
    override_name:str|None=None,
    hook_builder: hook_builder_module.AgentRuntimeHookBuilder|None=None,
    observer:base_observer_module.RabbitObserver|None=None,
    on_progress: agent_toolkit_builder.AgentToolkitProgressCallback | None = None,
) -> agent_module.ReActAgent:
    """把一个 ``AgentRuntime`` 声明装配成可运行的 ``ReActAgent``。

    整条流水线：选项 → 工具集 → LLM 配置/模型 → 记忆工具 → 提示词 →
    会话上下文 → 计划运行时 → 实例化 → 收尾（hook/observer/agent_context）。

    宿主（``AgentFactory``）上的能力是逐步补齐的，因此这里对每一项宿主能力都
    走 ``_invoke_optional``：宿主没装配就只是对应能力缺席并记一条日志，不让
    装配流程本身失败。
    """
    # on_progress 是新命名，on_process 是既有调用点的命名，两者都收。
    sub_agent.ensure_ready()
    options=_options(sub_agent, host.app_instance)
    toolkit_assembly = await host._agent_toolkit_builder.prepare_toolkit_assembly(
        sub_agent,
        on_progress= on_progress,
        include_builtin_tools=options.include_builtin_tools,
        include_mcp_tools=options.include_mcp_tools,
    )
    await asyncio.sleep(0)
    toolkit = toolkit_assembly.toolkit
    effective_llm_config = _resolve_effective_llm_config(host,sub_agent)
    model = host._model_context_factory.create_model(
        llm_config=effective_llm_config,
        agent_name=override_name or sub_agent.name,
        enable_token_tracking=True,
    )
    host._token_counter = host._model_context_factory.get_token_counter()
    agent_role=agent_role_module.AgentRole.resolve(sub_agent.agent_role,sub_agent.agent_slot)
    memory_runtime = _attach_memory_tools(host,toolkit,agent_role,sub_agent)
    _attach_rag_tools(host,toolkit)
    formatter = host._model_context_factory.create_formatter(
        llm_config= effective_llm_config,
        agent_name=override_name or sub_agent.name,
        enable_token_tracking=True,
    )
    model_context_window_size = context_window_resolver.get_effective_context_window_for_model(
        effective_llm_config.model_name,
        configured_context_window_size=effective_llm_config.context_window_size,
        metadata=effective_llm_config.metadata,
    )
    agent_id= override_name or sub_agent.name
    agent_context_state = _resolver_conversation_context(host,agent_id)
    system_prompt = await _build_system_prompt(
        host,
        sub_agent,
        options,
        agent_role,
        memory_runtime,
        override_name,
    )

    agent_name = host._agent_instance_manager.determine_agent_name(host, sub_agent, override_name)
    host.register_plugin_tools(
        toolkit=toolkit,
        slot=sub_agent.agent_slot,
        agent_name=sub_agent.name,
        allowed_tool_names=agent_toolkit_builder.AgentToolkitBuilder._normalize_allowed_tool_names(
            sub_agent.allowed_tools
        ),
    )

    diagnostic_context = _create_diagnostic_context(agent_name)
    plan_runtime= _resolve_plan_runtime(host.app_instance, options.include_plan_runtime)
    if plan_runtime is not None:
        _register_plan_runtime_tools(
            toolkit,
            plan_runtime,
            allowed_tool_names=agent_toolkit_builder.AgentToolkitBuilder._normalize_allowed_tool_names(
                sub_agent.allowed_tools
            ),
        )

    agent=_instantiate_agent(
        host,
        react_agent_factory=react_agent_factory,
        sub_agent=sub_agent,
        options=options,
        agent_name=agent_name,
        system_prompt=system_prompt,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        agent_context_state=agent_context_state,
        diagnostic_context=diagnostic_context,
        plan_runtime=plan_runtime,
        tool_classification_resolver=toolkit_assembly.tool_classification_resolver,
        model_id = _optional_attr(effective_llm_config, "model_name"),
        model_profile_id=_optional_attr(effective_llm_config, "model_profile_id"),
        model_context_window_size=model_context_window_size,
    )
    _finalize_agent(
        host,
        agent=agent,
        agent_name=agent_name,
        sub_agent=sub_agent,
        options=options,
        agent_role=agent_role,
        system_prompt=system_prompt,
        plan_runtime=plan_runtime,
        hook_builder=hook_builder,
        observer=observer,
    )
    return agent


def _slot_flag(
    slot: typing.Any,
    names: typing.Sequence[str],
    default: bool,
) -> bool:
    """按候选名依次读槽位上的布尔开关，读不到就用默认值。

    槽位模型（``AgentSlot``）与这里的开关名出现过两套拼写：
    ``include_plan_runtime`` / ``include_plan_tools``、
    ``parrallel_tool_calls``（沿用 ``ReActAgent`` 的历史拼写）/
    ``parallel_tool_calls``。硬取其中一个名字，另一套就会在装配时抛
    ``AttributeError``，而这类「声明里没这个字段」的错往往要等到真实装配才暴露。
    所以这里按候选名取第一个真正存在的布尔值。
    """
    if slot is None:
        return default
    for name in names:
        value = getattr(slot, name, None)
        if isinstance(value, bool):
            return value
    return default


def _options(
    sub_agent: agent_runtime_module.AgentRuntime,
    app:  AgentRuntimeApplicationPort| None,
) -> AgentRuntimeOptions:
    """从 sub_agent 声明与宿主全局配置里解析装配开关。

    宿主未装配、sub_agent 未声明时一律退回内置默认（全开基础能力），
    保证 agent 至少能被装配出来。
    """
    slot= sub_agent.agent_slot
    return AgentRuntimeOptions(
        include_builtin_tools=slot.include_builtin_tools if slot is not None else True,
        include_mcp_tools=slot.include_mcp_tools if slot is not None else True,
        include_plan_runtime=slot.include_plan_runtime if slot is not None else False,
        max_iters=slot.max_iters if slot is not None else sub_agent.max_iters,
        parallel_tool_calls=slot.parallel_tool_calls if slot is not None else True,
        # ``sytem_prompt_file`` 只是读取侧的兼容别名，构造参数用的是正确拼写。
        system_prompt_file=slot.system_prompt_file if slot is not None else "",
        project_home=app.user_project_home if app is not None else None,
    )


# 不同来源对同一字段的叫法：左边是来源键，右边是 ``LLMConfig`` 的字段名。
_LLM_CONFIG_FIELD_ALIASES: dict[str, str] = {
    "provider": "provider_values",
    "id": "profile_id",
    "model_profile_id": "profile_id",
    "configured_context_window_size": "context_window_size",
    "context_window": "context_window_size",
    "context_window_for_model": "context_window_size",
    "thinking": "thinking_mode",
}


def _project_llm_config(
    config: agent_runtime_module.AgentRuntimeModelConfig,
) -> llm_config_module.LLMConfig :
    """把 sub_agent 声明的模型配置"投影"成统一的 ``LLMConfig``。

    声明可能是 ``LLMConfig`` 本体、``LLMConfigProvider``、dict、任意
    dataclass/pydantic 对象，甚至 ``None``。这里统一收敛成 ``LLMConfig``：

    - 已经是 ``LLMConfig`` → 原样返回；
    - 有 ``to_llm_config()``（``LLMConfigProvider``）→ 交给它转换；
    - 其余形态 → 取它认识的字段做一次字段投影。

    ``LLMConfig`` 是 ``extra="forbid"`` 的严格模型，所以只投递它声明过的
    字段，陌生字段一律丢弃，避免整份配置因一个多余键校验失败。
    拿不到任何有效字段时返回 ``None``，由调用方决定兜底策略。
    """
    # 延迟导入：顶层导入会经 app/__init__ 反向拉起 agent_factory，造成循环导入。
    # 模块级只在 TYPE_CHECKING 下引入（供注解使用），运行期必须在这里补一次，
    # 否则 NameError: name 'llm_config_module' is not defined。
    import domains.configuration.domain.model.llm_config as llm_config_module

    if isinstance(config, llm_config_module.LLMConfig):
        return config

    return llm_config_module.LLMConfig(
        profile_id=config.profile_id,
        provider= config.provider,
        base_url= config.base_url,
        model_name= config.model_name,
        api_key= config.api_key,
        temperature= config.temperature,
        reasoning_effort= config.reasoning_effort,
        service_tier= config.service_tier,
        max_tokens= config.max_tokens,
        context_window_size= config.context_window_size,
        http_proxy= config.http_proxy,
        https_proxy= config.https_proxy,
        use_proxy= config.use_proxy,
        proxy_password= config.proxy_password,
        proxy_username= config.proxy_username,
        metadata= config.metadata,
        thinking_mode=config.thinking_mode,
        api_protocol= config.api_protocol,
        custom_headers=getattr(config, "custom_headers", {}),
    )



def _resolve_effective_llm_config(
    host: AgentRuntimeApplicationPort,
    sub_agent: agent_runtime_module.AgentRuntime,
) -> llm_config_module.LLMConfig:
    base_config= _project_llm_config(sub_agent.llm_config)
    if sub_agent.model_id is not None:
        llm_configs = host.config_manager.get_property("llm_configs",[]) or []
        configured_llm = runtime_model_config.find_llm_config_by_id(
            llm_configs=llm_configs,
            model_id=sub_agent.model_id,
            global_proxy_configuration= runtime_model_config.read_global_proxy_configuration(
                host.config_manager
            ),
        )
        if configured_llm is None:
            raise RuntimeError(f"Expert is bound to unavalible model")
        return configured_llm
    try:
        skill_name=sub_agent.skill_name or sub_agent.skill_name
        role=sub_agent.agent_role
        is_mode_root = bool(role and role.uses_mode_prompt_file)
        if not sub_agent.is_skill_based or is_mode_root:
            return base_config
        llm_configs= host.config_manager.get_property("llm_configs",[]) or []
        participant_refs = host.config_manager.get_property("participant_model_refs",{}) or {}
        if not participant_refs.get(skill_name) or not llm_configs:
            return base_config
        resolved = runtime_model_config.find_llm_config_from_profiles(
            llm_configs=llm_configs,
            skill_name=skill_name,
            participant_refs=participant_refs,
            default_config=base_config,
            global_proxy_configuration= runtime_model_config.read_global_proxy_configuration(
                host.config_manager
            ),
        )
        if resolved is None:
            return base_config
        return resolved
    except  Exception as e:
        return base_config

def _attach_memory_tools(
    host: AgentRuntimeApplicationPort,
    toolkit: toolkit_module.Toolkit,
    role: agent_role_module.AgentRole,
    sub_agent: agent_runtime_module.AgentRuntime|None=None,
) -> memory_runtime_service.MemoryRuntimeService | None:
    """把记忆工具挂进工具集，并返回生效的记忆运行时。

    - 宿主未装配 ``memory_runtime``、或记忆功能被显式关闭 → 返回 ``None``。
    - 记忆运行时未暴露可注册的工具 → 只返回运行时，不挂工具。
    """
    base_runtime= host.app_instance.memory_runtime if host.app_instance is not None else None
    if base_runtime is None or not should_attach_memory(role, base_runtime):
        return None
    runtime= base_runtime
    if(
        sub_agent is not None
        and sub_agent.expert_memory_file is not None
        and sub_agent.expert_memory_file.is_absolute()
    ):
        expert_settings= base_runtime.settings.model_copy(
            update={"expert_memory_file":sub_agent.expert_memory_file},
        )
        runtime= memory_runtime_service.MemoryRuntimeService(expert_settings)
    memory_agent_tools.register_memory_tools(toolkit, runtime)


def _attach_rag_tools(
    host: AgentRuntimeApplicationPort,
    toolkit: toolkit_module.Toolkit,
) -> typing.Any | None:
    """把 RAG 工具挂进工具集。

    与记忆工具分开处理，因为二者依赖的外部条件不同：

    - 记忆（Markdown 文档轨）只需要本地文件系统，默认可用；
    - RAG 需要外部 Qdrant 服务，因此**默认关闭**，由配置显式开启
      （``personalization.rag.enabled``）。

    开关与运行参数取自分层合并后的配置快照
    （``app_instance.config.runtime_config_snapshot``）；取不到配置时按
    「未启用」处理——宁可少一个工具，也不要在无 Qdrant 的环境里注册一个
    必然失败的工具让模型反复试错。
    """
    if host.app_instance is None:
        return None
    config = getattr(host.app_instance, "config", None)
    snapshot = getattr(config, "runtime_config_snapshot", None)
    if not isinstance(snapshot, typing.Mapping):  # type: ignore[arg-type]
        return None
    settings = rag_config_rules.resolve_rag_settings(snapshot)
    if not settings.get("enabled"):
        return None
    return rag_agent_tools.register_rag_tools(toolkit, settings)

    return runtime

def should_attach_memory(
    role: agent_role_module.AgentRole,
    runtime: memory_runtime_service.MemoryRuntimeService|None=None,
)->bool:
    return role.uses_mode_prompt_file and runtime is not None and runtime.settings.enabled

async def _build_system_prompt(
    host: AgentRuntimeApplicationPort,
    sub_agent: agent_runtime_module.AgentRuntime,
    options: AgentRuntimeOptions,
    role: agent_role_module.AgentRole,
    memory_runtime: memory_runtime_service.MemoryRuntimeService | None,
    override_name: str | None,
) -> str:
    """构造 agent 的系统提示词。

    宿主未装配 ``_system_prompt_builder``（或它还没有 build 方法）时返回空串，
    让 agent 以无系统提示的状态装配，由上层决定是否降级。
    """
    runtime_environment = process_runtime_context.get_runtime_context_or_none()
    system_config_dir=runtime_environment.system_config_dir if runtime_environment is not None else None
    additional_sections:list[str]=[]
    if(
        host.app_instance is not None
        and sub_agent.include_agent_tools
        and role is not agent_role_module.AgentRole.SUB_AGENT
        and role is not agent_role_module.AgentRole.ORCHESTRATOR
    ):
        additional_sections.append(
            expert_call_service.ExpertCallService(
                host.app_instance,
                sub_agent,
            ).build_prompt_section()
        )
    try:
        system_prompt=await host._system_prompt_builder.build(
            role=role,
            sub_agent=sub_agent,
            system_prompt_file = options.system_prompt_file,
            project_home = options.project_home,
            system_config_dir=system_config_dir,
            additional_sections=additional_sections,
            optimize=True,
        )
    except Exception as e:
        logger.error(e)
        system_prompt = sub_agent.system_prompt or ""
    system_prompt = await _append_memory(system_prompt, role,memory_runtime)
    system_prompt = await _append_plugin_transform(system_prompt,sub_agent, role,override_name)
    return system_prompt

async def _append_memory(
        system_prompt: str,
        role: agent_role_module.AgentRole,
        memory_runtime: memory_runtime_service.MemoryRuntimeService|None=None,
)->str:
    if memory_runtime is None or not should_attach_memory(role, memory_runtime):
        return system_prompt
    projection= await asyncio.to_thread(memory_runtime.prompt_projection)
    section=memory_agent_tools.build_memory_prompt_section(projection)
    return f"{system_prompt}\n\n---\n\n{section}" if system_prompt else section

async def _append_plugin_transform(
        system_prompt: str,
        sub_agent: agent_runtime_module.AgentRuntime,
        role: agent_role_module.AgentRole,
        override_name: str | None=None,
)->str:
    if not role.uses_mode_prompt_file:
        return system_prompt
    slot = sub_agent.agent_slot
    agent_name=slot.name if slot is not None else override_name or sub_agent.name
    return await plugin_prompt_transform.append_plugin_system_transform(
        system_prompt,
        plugin_manager = plugin_context.get_plugin_manager(),
        agent_name = agent_name,
        agent_role = role,
        slot = slot,
        log_context="AGENT_FACTORY",
    )


def _register_plan_runtime_tools(
    toolkit: typing.Any | None,
    plan_runtime: plan_runtime_module.PlanRuntime,
    allowed_tool_names: typing.Collection[str] | None,
) -> None:
    """把计划运行时暴露的工具挂进工具集。

    ``plan_runtime`` 尚未实现（``plan.application.plan_runtime`` 目前为空模块）时
    这里不会有工具可挂，直接返回。
    """
    if toolkit is None or plan_runtime is None:
        return

    tools = _collect_tools(plan_runtime)
    if not tools:
        logger.debug(
            "plan_runtime %s 未暴露可注册工具，跳过", type(plan_runtime).__name__
        )
        return

    _register_tools(
        toolkit,
        tools,
        allowed_tool_names=allowed_tool_names,
        source="plan_runtime",
    )


def _create_diagnostic_context(agent_name: str) -> AgentDiagnosticContext:
    """创建 agent 的诊断上下文，并构造真正可变的 ``AgentContext``。

    ``AgentContext`` 的两个必填字段是 ``agent_id`` 与 ``session_id``；它同时是
    ``extra="forbid"`` 的pydantic 模型，多传一个字段就会让整个构造抛
    ``ValidationError``。

    原代码额外传了 ``agent_name=`` 与 ``name=`` 两个**并不存在**的字段，构造
    100% 失败并被降级成 ``agent_context=None``。后果是
    ``ReActAgent.__init__`` 的"没有 agent_context 就raise"断言被触发，整条 agent
    创建链失败。这里改为只传合法字段，``session_id`` 取当前会话（取不到时用
    agent 名占位，保证构造一定成功）。

    构造仍失败时降级为 ``None``，但把原因升级为 ``warning`` 便于排障。
    """
    agent_context = None
    factory = getattr(agent_context_module, "AgentContext", None)
    if factory is not None:
        try:
            session_id = ""
            try:
                from domains.agent.domain.aggregates.execution_context import get_session_id

                session_id = get_session_id() or ""
            except Exception:  # noqa: BLE001 - 会话上下文未安装时用占位值
                session_id = ""
            agent_context = factory(
                agent_id=agent_name,
                session_id=session_id or agent_name,
            )
        except Exception as exc:  # noqa: BLE001 - 降级但保留可读原因
            logger.warning(
                "为 agent %r 构造 AgentContext 失败，诊断上下文降级为 None: %s",
                agent_name,
                exc,
            )
            agent_context = None

    return AgentDiagnosticContext(
        agent_name=agent_name,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        agent_context=agent_context,
    )

def _scheduler_resolver(
    host:AgentRuntimeCompositionHost,
    diagnostic_context:agent_context_module.AgentContext|None,
)->runtime_ports.AgentSchedulerResolverProtocol|None:
    if diagnostic_context is None:
        return None
    def resolve_current_scheduler()->agent_scheduler.AgentScheduler|None:
        return host.resolve_agent_scheduler(diagnostic_context)
    return resolve_current_scheduler

def _instantiate_agent(
    host: AgentRuntimeApplicationPort,
    *,
    react_agent_factory: collections.abc.Callable[..., agent_module.ReActAgent],
    sub_agent: agent_runtime_module.AgentRuntime,
    options: AgentRuntimeOptions,
    agent_name: str,
    system_prompt: str,
    model: typing.Any | None,
    formatter: typing.Any | None,
    toolkit: typing.Any | None,
    agent_context_state: typing.Any | None,
    diagnostic_context: AgentDiagnosticContext,
    plan_runtime: typing.Any | None,
    tool_classification_resolver: typing.Any | None,
    model_id: str | None,
    model_profile_id: str | None,
    model_context_window_size: int | None,
) -> agent_module.ReActAgent:
   scheduler_resolver = _scheduler_resolver(host, diagnostic_context)
   hook_agent_source_path =str(sub_agent.source_path) if sub_agent.source_path is not None else None
   hook_agent_type= sub_agent.name if hook_agent_source_path is not None else None

   tool_call_listener=None
   if sub_agent.tool_call_listener_id:
       tool_call_listener = host.tool_call_listener(
           sub_agent.tool_call_listener_id,
       )
   if plan_runtime is not None:
       return react_agent_factory(
           name=agent_name,
           sys_prompt=system_prompt,
           model=model,
           formatter=formatter,
           toolkit=toolkit,
           context=agent_context_state,
           max_iters= options.max_iters,
           plan_runtime=plan_runtime,
           parallel_tool_calls = options.parallel_tool_calls,
           # 同上：要传真正可变的 AgentContext，而非 frozen 的诊断外壳。
           agent_context =diagnostic_context.agent_context,
           tool_classification_resolver=tool_classification_resolver,
           model_id=model_id,
           model_profile_id=model_profile_id,
           model_context_window_size=model_context_window_size,
           execution_store=host._execution_store,
   scheduler_resolver=scheduler_resolver,
   hook_agent_type=hook_agent_type,
   hook_agent_source_path=hook_agent_source_path,
           # ``tool_call_mode`` —— 原拼写 ``tool_callmode`` 与
           # ``ReActAgent.__init__`` 的关键字不一致，会直接 TypeError；
           # 这里是「工具调用走 native 还是文本解析」的唯一开关。
           tool_call_mode = sub_agent.tool_call_mode,
           # ``preset_type`` —— 6 个独立 agent 类降级后的配置预设值，
           # 决定 ReActAgent 内部走哪条回复策略（见 _reply_impl）。
           preset_type = sub_agent.preset_type,
           reflection_max_iterations = sub_agent.reflection_max_iterations,
           reflection_prompts = sub_agent.reflection_prompts,
           plan_prompts = sub_agent.plan_prompts,
           tool_call_listener=tool_call_listener,
           enable_meta_tool=sub_agent.enable_meta_tool,
   )
   return host._agent_instance_manager.create_agent_instance(
       agent_name=agent_name,
       system_prompt=system_prompt,
       model=model,
       formatter=formatter,
       toolkit=toolkit,
       context=agent_context_state,
       max_iters=options.max_iters,
       enable_meta_tool=True,
       parallel_tool_calls=options.parallel_tool_calls,
       # ``ReActAgent.agent_context`` 要的是**可变的**
       # ``agent.model.agent_context.AgentContext``（运行期会写
       # ``iteration`` / ``status`` / ``last_heartbeat``）。原先这里错传了
       # frozen dataclass ``AgentDiagnosticContext``，于是运行期每次记账都抛
       # ``dataclasses.FrozenInstanceError: cannot assign to field 'iteration'``，
       # 被宽 except 吞成一行 ERROR 日志。真正的 AgentContext 早已由
       # ``_create_diagnostic_context`` 构造好，就放在
       # ``diagnostic_context.agent_context`` 里——这里应该取它。
       agent_context=diagnostic_context.agent_context,
       tool_classification_resolver=tool_classification_resolver,
       model_id=model_id,
       model_profile_id=model_profile_id,
       model_context_window_size=model_context_window_size,
       execution_store=host._execution_store,
       scheduler_resolver=scheduler_resolver,
       hook_agent_type=hook_agent_type,
       hook_agent_source_path=hook_agent_source_path,
       tool_call_mode=sub_agent.tool_call_mode,
       preset_type=sub_agent.preset_type,
       reflection_max_iterations=sub_agent.reflection_max_iterations,
       reflection_prompts=sub_agent.reflection_prompts,
       plan_prompts=sub_agent.plan_prompts,
       tool_call_listener=tool_call_listener,
   )


def _attach_plan_hook(
    host: AgentRuntimeApplicationPort,
    agent: agent_module.ReActAgent,
    plan_runtime: plan_runtime_module.PlanRuntime | None,
    observer: base_observer_module.RabbitObserver | None,
) -> None:
    """把计划运行时挂到 agent 上，并把计划变更接到 observer。

    两步都可缺席：没有 ``plan_runtime``（非计划型 agent）直接返回；运行时没提供
    ``attach*`` 入口也只记 debug——计划能力缺失不该让已构造好的 agent 报废。
    """
    if plan_runtime is None:
        return

    try:
        if getattr(agent, "plan_runtime", None) is None:
            agent.plan_runtime = plan_runtime
    except Exception:  # noqa: BLE001
        logger.debug("无法回填 agent %r 的 plan_runtime", getattr(agent, "name", None))

    for name in ("attach_agent", "attach", "bind_agent", "register_agent"):
        attach = getattr(plan_runtime, name, None)
        if not callable(attach):
            continue
        try:
            attach(agent)
            break
        except TypeError:
            continue
        except Exception:  # noqa: BLE001
            logger.debug("plan_runtime.%s 失败", name, exc_info=True)
            break

    if observer is None:
        return
    for name in ("set_observer", "bind_observer", "attach_observer"):
        setter = getattr(plan_runtime, name, None)
        if not callable(setter):
            continue
        try:
            setter(observer)
            return
        except TypeError:
            continue
        except Exception:  # noqa: BLE001
            logger.debug("plan_runtime.%s 失败", name, exc_info=True)
            return


def _finalize_agent(
    host: AgentRuntimeApplicationPort,
    *,
    agent: agent_module.ReActAgent,
    agent_name: str,
    sub_agent: agent_runtime_module.AgentRuntime,
    options: AgentRuntimeOptions,
    agent_role: agent_role_module.AgentRole,
    system_prompt: str,
    plan_runtime: plan_runtime_module.PlanRuntime | None,
    hook_builder: hook_builder_module.AgentRuntimeHookBuilder | None,
    observer: base_observer_module.RabbitObserver | None,
) -> None:
    """agent 实例化之后的收尾装配：agent_context / observer / hook / 计划运行时。

    每一步都可缺席——缺什么只是对应能力不可用，不让已构造好的 agent 报废。
    """
    if host.app_instance is not None:
        host.materialize_agent_runtime_identity(agent_id=agent_name,sub_agent=sub_agent,runtime=agent)
        agent.bind_context_persistence(host.app_instance.persist_agent_context_strict)
    host._agent_instance_manager.configure_agent_metadata(
        agent=agent,
        original_system_prompt=sub_agent.system_prompt,
        optimized_system_prompt=system_prompt,
        optimization_applied= system_prompt != sub_agent.system_prompt,
    )
    host._hook(agent)
    _attach_plan_hook(host,agent,plan_runtime, observer)
    resolved_hook_builder= hook_builder or hook_builder_module.AgentRuntimeHookBuilder(
        token_counter=host._token_counter,
        config_manager=host.config_manager,
        session_id_func=execution_context.get_session_id,
        observer_instance= host.observer,
    )
    available_tools:list[str]|None=None
    if agent_role.uses_mode_prompt_file or options.include_plan_runtime:
        available_tools = _extract_tool_schema_names(agent.toolkit.get_json_schemas())
    resolved_hook_builder.attach_all_hooks(
        agent=agent,
        is_root_agent=options.include_plan_runtime and not options.include_builtin_tools,
        available_tools=available_tools,
    )
    plugin_manager= plugin_context.get_plugin_manager()
    if plugin_manager and plugin_manager.has_active_plugins():
        plugin_manager.attach_hooks_to_agent(agent)
    host._register_incremental_save_hook(agent)


def _resolver_conversation_context(
    host: AgentRuntimeApplicationPort,
    agent_id: str,
) -> conversation_context_module.Context | None:
    """为 agent 解析/创建会话上下文（``ReActAgent`` 的 ``context`` 参数）。

    优先复用宿主/应用级会话上下文里按 agent 派生的上下文，
    拿不到就新建 ``conversation_context.Context``；都失败则返回 ``None``。
    """
    container = getattr(host, "conversation_context", None)
    if container is None:
        container = _optional_attr(
            _optional_attr(host, "app_instance"), "conversation_context"
        )

    if container is not None:
        for attr in ("for_agent", "get_context", "context_for", "resolve"):
            resolver = getattr(container, attr, None)
            if not callable(resolver):
                continue
            try:
                context = _invoke_with_supported_kwargs(
                    resolver,
                    agent_id=agent_id,
                    agent_name=agent_id,
                    name=agent_id,
                )
            except Exception:
                logger.debug("会话上下文容器 %s 调用失败", attr, exc_info=True)
                continue
            if context is not None:
                return context

    factory = getattr(conversation_context_module, "Context", None)
    if factory is not None:
        try:
            return _invoke_with_supported_kwargs(
                factory,
                agent_id=agent_id,
                agent_name=agent_id,
                name=agent_id,
            )
        except Exception:
            logger.debug("构造 conversation_context.Context 失败", exc_info=True)

    logger.warning("无法为 agent %r 解析会话上下文，将以无上下文状态运行", agent_id)
    return None


def _resolve_plan_runtime(
    app_instance: typing.Any | None,
    include_plan_runtime: bool,
) -> typing.Any | None:
    """按开关决定是否取宿主上的计划运行时。"""
    if not include_plan_runtime:
        return None
    plan_runtime = getattr(app_instance, "plan_runtime", None)
    if plan_runtime is None:
        logger.debug("未装配 plan_runtime，该 agent 不具备计划能力")
    return plan_runtime


# ---------------------------------------------------------------------------
# 内部兼容工具：宿主能力在逐步补齐，下面这些 helper 保证"缺项降级"而不是崩溃。
# ---------------------------------------------------------------------------


def _optional_attr(target: typing.Any | None, name: str, default: object = None) -> object:
    """取属性，取不到（或目标为 ``None``）时返回默认值。"""
    if target is None:
        return default
    return getattr(target, name, default)


def _invoke_optional(
    target: typing.Any | None,
    name: str,
    *args: object,
    **kwargs: object,
) -> object:
    """调用 ``target`` 上的可选能力；该能力未装配时返回 ``None``。"""
    if target is None:
        return None
    method = getattr(target, name, None)
    if method is None:
        logger.debug(
            "%s 未装配可选能力 %s，跳过", type(target).__name__, name
        )
        return None
    if not callable(method):
        return method
    return method(*args, **kwargs)


def _invoke_with_supported_kwargs(fn: collections.abc.Callable[..., typing.Any], **candidates: object) -> object:
    """按 ``fn`` 真实接受的形参挑参数后调用。

    各模块的实现还在演进，调用点无法保证参数名始终一致；这里只传它能接的。
    """
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn()

    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return fn(**candidates)

    supported = {k: v for k, v in candidates.items() if k in parameters}
    return fn(**supported)


def _accepted_parameters(factory: collections.abc.Callable[..., typing.Any]) -> set[str] | None:
    """取工厂（类或其 ``__init__``）能接受的形参名集合。

    ``inspect.signature`` 在 ReActAgent 上会因注解求值失败（runtime_ports 里
    的协议还没补齐），因此回退到直接读 ``__code__`` 的参数名。
    拿不到时返回 ``None``，表示"不筛选，全传"。
    """
    try:
        return set(inspect.signature(factory).parameters)
    except Exception:
        pass

    is_class = inspect.isclass(factory)
    target = getattr(factory, "__init__", factory) if is_class else factory
    code = getattr(target, "__code__", None)
    if code is None:
        return None

    start = 1 if is_class else 0
    positional_end = start + code.co_argcount
    keyword_end = positional_end + code.co_kwonlyargcount
    return set(code.co_varnames[start:keyword_end])


def _collect_tools(source: typing.Any | None) -> list[typing.Any]:
    """从不同形态的运行时上收集工具列表。"""
    if source is None:
        return []

    for attr in ("build_tools", "create_tools", "tools", "plan_tools"):
        candidate = getattr(source, attr, None)
        if candidate is None:
            continue
        if isinstance(candidate, (list, tuple, set, frozenset)):
            return list(candidate)
        if callable(candidate):
            try:
                produced = _invoke_with_supported_kwargs(candidate)
            except Exception:
                logger.debug("%s.%s 调用失败", type(source).__name__, attr, exc_info=True)
                continue
            if isinstance(produced, (list, tuple, set, frozenset)):
                return list(produced)
    return []


def _tool_name(tool: typing.Any) -> str | None:
    """取工具名，兼容 ``tool.name`` / ``tool.tool_name`` / 字典形态。"""
    if isinstance(tool, collections.abc.Mapping):
        name = tool.get("name")
        return str(name) if name is not None else None
    for attr in ("name", "tool_name", "__name__"):
        name = getattr(tool, attr, None)
        if isinstance(name, str):
            return name
    return None


def _register_tools(
    toolkit: typing.Any | None,
    tools: typing.Sequence[typing.Any],
    *,
    allowed_tool_names: typing.Collection[str] | None,
    source: str,
) -> int:
    """把工具注册进工具集，返回成功注册的个数。"""
    if toolkit is None:
        logger.warning("工具集未装配，%s 的 %d 个工具未挂载", source, len(tools))
        return 0

    register = None
    for attr in ("register_tool", "add_tool", "register"):
        candidate = getattr(toolkit, attr, None)
        if callable(candidate):
            register = candidate
            break
    if register is None:
        logger.warning(
            "工具集 %s 未提供注册方法，%s 的 %d 个工具未挂载",
            type(toolkit).__name__, source, len(tools),
        )
        return 0

    registered = 0
    for tool in tools:
        name = _tool_name(tool)
        if allowed_tool_names and name is not None and name not in allowed_tool_names:
            logger.debug("工具 %s 不在 allowed_tools 白名单内，跳过", name)
            continue
        try:
            if name is None:
                register(tool)
            else:
                try:
                    register(tool)
                except TypeError:
                    register(name, tool)
        except Exception:
            logger.exception("注册 %s 的工具 %s 失败", source, name)
            continue
        registered += 1
    return registered


def _normalized_allowed_tools(
    sub_agent: agent_runtime_module.AgentRuntime,
) -> typing.Collection[str] | None:
    """归一化 sub_agent 的工具白名单；未声明时返回 ``None``（表示不限制）。"""
    raw = _optional_attr(sub_agent, "allowed_tools")
    normalize = getattr(
        agent_toolkit_builder.AgentToolkitBuilder, "_normalize_allowed_tool_names", None
    )
    if callable(normalize):
        try:
            return normalize(raw)
        except Exception:
            logger.debug("归一化 allowed_tools 失败，按原值处理", exc_info=True)
    if not raw:
        return None
    return {str(item) for item in raw}


def _resolve_agent_role(
    sub_agent: agent_runtime_module.AgentRuntime,
) -> agent_role_module.AgentRole:
    """解析 agent 角色。

    ``AgentRole.resolve`` 尚未实现（当前 ``AgentRole`` 只是 StrEnum），
    此时退回 sub_agent 声明的角色，再退回 ``GENERAL``。
    """
    resolve = getattr(agent_role_module.AgentRole, "resolve", None)
    if callable(resolve):
        try:
            return resolve(
                _optional_attr(sub_agent, "agent_role"),
                _optional_attr(sub_agent, "agent_slot"),
            )
        except Exception:
            logger.debug("AgentRole.resolve 调用失败，退回声明值", exc_info=True)

    declared = _optional_attr(sub_agent, "agent_role")
    if declared is None:
        return agent_role_module.AgentRole.GENERAL
    try:
        return agent_role_module.AgentRole(declared)
    except ValueError:
        logger.debug("未知 agent_role %r，退回 GENERAL", declared)
        return agent_role_module.AgentRole.GENERAL


def _determine_agent_name(
    host: AgentRuntimeApplicationPort,
    sub_agent: agent_runtime_module.AgentRuntime,
    override_name: str | None,
) -> str:
    """确定 agent 最终名字。

    优先 ``_agen_instance_manager.determine_agent_name``（宿主拼写为
    ``_agent_instance_manager``，两种都认），缺失时按 override→声明名→类型名兜底。
    """
    manager = getattr(host, "_agen_instance_manager", None)
    if manager is None:
        manager = getattr(host, "_agent_instance_manager", None)

    determine = getattr(manager, "determine_agent_name", None)
    if callable(determine):
        try:
            name = _invoke_with_supported_kwargs(
                determine,
                sub_agent=sub_agent,
                agent_runtime=sub_agent,
                override_name=override_name,
            )
        except Exception:
            logger.debug("determine_agent_name 调用失败，退回兜底命名", exc_info=True)
        else:
            if isinstance(name, str) and name:
                return name

    name = (
        override_name
        or _optional_attr(sub_agent, "name")
        or _optional_attr(sub_agent, "agent_name")
    )
    if isinstance(name, str) and name:
        return name
    return type(sub_agent).__name__


async def _prepare_toolkit_assembly(
    host: AgentRuntimeApplicationPort,
    sub_agent: agent_runtime_module.AgentRuntime,
    options: AgentRuntimeOptions,
    on_progress: agent_toolkit_builder.AgentToolkitProgressCallback | None,
) -> typing.Any:
    """准备工具集装配结果。

    ``AgentToolkitBuilder.prepare_toolkit_assembly`` 还没实现时返回空装配，
    agent 以空工具集继续装配。
    """
    builder = getattr(host, "_agent_toolkit_builder", None)
    prepare = getattr(builder, "prepare_toolkit_assembly", None)
    if not callable(prepare):
        logger.warning(
            "宿主未装配 _agent_toolkit_builder.prepare_toolkit_assembly，"
            "agent %r 以空工具集运行",
            _optional_attr(sub_agent, "name"),
        )
        return _FallbackToolkitAssembly()

    result = _invoke_with_supported_kwargs(
        prepare,
        sub_agent=sub_agent,
        on_progress=on_progress,
        include_builtin_tools=options.include_builtin_tools,
        include_mcp_tools=options.include_mcp_tools,
    )
    if inspect.isawaitable(result):
        result = await result
    return result


def _attach_hooks(
    agent: agent_module.ReActAgent,
    hook_builder: hook_builder_module.AgentRuntimeHookBuilder,
    agent_name: str,
) -> None:
    """把 hook_builder 产出的 hook 挂到 agent 上。

    ``agent.service.agent_runtime_hook_builder`` 目前还是空模块，这里对
    build/attach 的产物形态（嵌套 dict、扁平 dict、序列）都做兼容。
    """
    hooks = None
    for attr in ("build", "build_hooks", "attach", "apply"):
        builder_fn = getattr(hook_builder, attr, None)
        if not callable(builder_fn):
            continue
        try:
            hooks = _invoke_with_supported_kwargs(
                builder_fn, agent=agent, agent_name=agent_name
            )
        except Exception:
            logger.exception("hook_builder.%s 执行失败", attr)
            hooks = None
            continue
        break

    if not hooks:
        return

    if isinstance(hooks, collections.abc.Mapping):
        for hook_type, bucket in hooks.items():
            if isinstance(bucket, collections.abc.Mapping):
                for hook_name, callback in bucket.items():
                    _register_hook(agent, hook_type, hook_name, callback)
            elif callable(bucket):
                _register_hook(agent, hook_type, str(hook_type), bucket)
    elif isinstance(hooks, collections.abc.Sequence):
        for index, callback in enumerate(hooks):
            if callable(callback):
                _register_hook(agent, "pre_rabbit", f"hook_{index}", callback)


def _extract_tool_schema_names(schemas: object) -> list[str]:
    """从工具 JSON Schema 清单里取出工具名。

    形态有两种，都要认：OpenAI 风格的 ``{"type":"function","function":{"name":...}}``
    和扁平的 ``{"name": ...}``。取不到名字的条目跳过——工具清单只用来给 hook
    提供 ``available_tools``，少一个比整轮崩掉好。
    """
    names: list[str] = []
    if schemas is None:
        return names
    if not isinstance(schemas, (list, tuple, set, frozenset)):
        schemas = [schemas]
    for item in schemas:
        if isinstance(item, str):
            if item:
                names.append(item)
            continue
        if not isinstance(item, collections.abc.Mapping):
            item = getattr(item, "__dict__", None)
            if not isinstance(item, collections.abc.Mapping):
                continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            nested = item.get("function")
            if isinstance(nested, collections.abc.Mapping):
                name = nested.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _register_hook(
    agent: agent_module.ReActAgent,
    hook_type: str,
    hook_name: str,
    callback: collections.abc.Callable[..., typing.Any],
) -> None:
    """注册单个 hook：优先用 agent 的公开方法，没有就直接写 ``_hooks``。"""
    register = getattr(agent, "register_hook", None)
    if callable(register):
        try:
            _invoke_with_supported_kwargs(
                register,
                hook_type=hook_type,
                name=hook_name,
                callback=callback,
            )
            return
        except Exception:
            logger.debug("register_hook 调用失败，回退直接写 _hooks", exc_info=True)

    hooks = getattr(agent, "_hooks", None)
    if not isinstance(hooks, dict):
        return
    bucket = hooks.get(hook_type)
    if bucket is None:
        if hook_type not in getattr(agent, "HOOK_TYPES", ()):
            logger.debug("忽略未知 hook 类型 %s（agent=%r）", hook_type, getattr(agent, "name", None))
            return
        bucket = {}
        hooks[hook_type] = bucket
    bucket[hook_name] = callback
