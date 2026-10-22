import inspect
import logging
import typing

import pydantic

import domains.runtime.application.orchestrators.agent_orchestration_contacts as orchestration_contracts
import domains.runtime.application.orchestrators.agent_bootstrap as agent_bootstrap
import domains.runtime.application.orchestrators.agent_configuration_loading as agent_configuration_loading
import domains.runtime.application.orchestrators.mode_agent_creation as mode_agent_creation
import domains.runtime.application.orchestrators.mode_orchestration as mode_orchestration
import domains.runtime.application.orchestrators.groupchat_agent_builder as groupchat_agent_builder
import domains.agent.domain.entities.agent_runtime as  agent_runtime_module
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.agent.application.loader.root_agent_loader as root_agent_loader_module
import domains.agent.application.loader.explicit_agent_loader as explicit_agent_loader_module
import domains.agent.application.loader.agent_factory as agent_factory_module
import domains.mcp.infrastructure.client_runtime as client_runtime
import domains.mcp.domain.tool_projection as tool_projection
import domains.skill.service.skill_registry as skill_registry_module
import domains.configuration.domain.runtime_state.system_resources as system_resources_module
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.configuration.domain.model.app_mode as app_mode_configuration
import domains.plugin.domain as contracts
from domains.plan.domain import execution_plan

ExplicitAgentLoader= explicit_agent_loader_module.ExplicitAgentLoader
RootAgentLoader= root_agent_loader_module.RootAgentLoader

logger = logging.getLogger(__name__)

AgentRuntimeSnapShot:typing.TypeAlias = dict[str,pydantic.JsonValue]
AgentFactory= agent_factory_module.AgentFactory
MCPManager = client_runtime.MCPManager

_MCP_TOOL_GATEWAY_PLUGIN_NAME="mcp_tool_gateway"
_AGENT_RUNTIME_SNAPSHOT_ADAPTER= pydantic.TypeAdapter(AgentRuntimeSnapShot)

def _resolve_mcp_tool_projection_mode(
    active_plugin_names: typing.Collection[str],
)->tool_projection.MCPToolProjectionMode:
    if _MCP_TOOL_GATEWAY_PLUGIN_NAME in active_plugin_names:
        return tool_projection.MCPToolProjectionMode.GATEWAY
    return tool_projection.MCPToolProjectionMode.EAGER


class AgentOrchestrator:

    def __init__(self, app: orchestration_contracts.AgentOrchestrationAppPort)->None:
        self.app = app
        self._configuration_loading = agent_configuration_loading.AgentConfigurationLoadingService()
        self._bootstrap = agent_bootstrap.AgentBootstrapService()
        self._mode_agent_creation = mode_agent_creation.ModeAgentCreationService()
        self._groupchat_agent_builder = groupchat_agent_builder.GroupChatAgentBuilder()
        self._mode_orchestration = mode_orchestration.ModeOrchestrationService()

    @property
    def llm_config(self)->typing.Any:
        """宿主的 LLM 配置。

        ``mode_agent_creation._build_runtime_config`` 是按 ``host.llm_config``
        取值的（宿主契约 ``AgentRuntimeApplicationPort`` 里也是这个名字），而编排器
        自身只持有 ``self.app``。这里做一次转发，避免同一份配置出现两种取法
        （``host.llm_config`` 与 ``host.app.llm_config``）——两者必须永远相等，
        否则改一处漏一处。
        """
        return self.app.llm_config

    def _system_skill_registry(self)->typing.Optional[skill_registry_module.SkillRegistry]:
        system_resources= self.app.config.system_resources
        if system_resources is None:
            return None
        return system_resources.skill_registry

    def _system_skill_registry_refresher(self)-> typing.Optional[skill_registry_module.SkillRegistryRefresher]:
        system_resources= self.app.config.system_resources
        if system_resources is None:
            return None
        return system_resources.skill_registry_refresher

    def _system_rabbit_skills_md(self)-> typing.Optional[str]:
        system_resources= self.app.config.system_resources
        if system_resources is None:
            return None
        return system_resources.rabbit_skills_md

    def _system_instruction_resources(self)-> typing.Sequence[system_resources_module.InstructionResource]|None:
        system_resources= self.app.config.system_resources
        if system_resources is None:
            return None
        return system_resources.instruction_resources

    def _install_subtask_auto_fix_hook(self)->None:
        self._ensure_subtasks_are_objects()

    def _ensure_subtasks_are_objects(self)->None:
        plan_runtime = self.app.plan_runtime
        if plan_runtime is None:
            return
        current_plan= plan_runtime.current_plan
        if not current_plan or not current_plan.subtasks:
            return
        raw_subtasks= typing.cast(list[object],list(current_plan.subtasks))
        if all(isinstance(subtask, execution_plan.Subtask) for subtask in raw_subtasks):
            return
        try:
            normalized_subtasks= [execution_plan.Subtask.model_validate(subtask) for subtask in raw_subtasks]
        except pydantic.ValidationError as exc:
            return
        current_plan.subtasks= normalized_subtasks

    def _load_agent_configs(
        self,
        selected_expert_ids: typing.Sequence[str]|None=None,
        selectec_skill_names:typing.Sequence[str]|None=None,
    )-> tuple[list[agent_runtime_module.AgentRuntime],
              root_agent_config_module.RootAgentConfig|None]:

        result = self._configuration_loading.load(
            self,
            sub_agent_loader_type=ExplicitAgentLoader,
            root_agent_loader_type=RootAgentLoader,
            selected_expert_ids=selected_expert_ids,
            selectec_skill_names=selectec_skill_names,
        )
        if result is None:
            return [], None
        return result

    # --- Agent 创建 -------------------------------------------------------

    async def _create_agents(
        self,
        sub_agent_configs: typing.Sequence[agent_runtime_module.AgentRuntime]|None=None,
        root_agent_config: root_agent_config_module.RootAgentConfig|None=None,
        on_progress: orchestration_contracts.ProcessCallback|None=None,
        *,
        defer_root_creation: bool=False,
    )-> None:
        await self._bootstrap.execute(
            self,
            sub_agent_configs=sub_agent_configs,
            root_agent_config=root_agent_config,
            agent_factory_type= AgentFactory,
            mcp_manager_type=MCPManager,
            projection_resolver= _resolve_mcp_tool_projection_mode,
            on_progress=on_progress,
            defer_root_creation=defer_root_creation,
        )

    # --- 创建流程的辅助步骤 -------------------------------------------------

    async def _initialize_all_sub_agents(self) -> None:
        """预加载当前模式下的全部子 agent。

        占位实现：子 agent 的实际创建改为按需触发（构建根 agent 时一并完成），
        这里不再在启动期主动预建，避免在能力未装配完整时做重活。真实预加载逻辑
        待本模块补齐子 agent 创建管线后落地。
        """
        return None

    @staticmethod
    def _is_config_instance(value: object)-> bool:
        """判断入参是「配置实例」而不是「配置类」。

        ``rabbit_application.py`` 的默认值写成了 ``root_agent_config=RootAgentConfig``
        （类本身），直接当成配置用会在后续读属性时炸在很深的调用栈里，这里提前拦掉。
        """
        return value is not None and not isinstance(value, type)

    def _resolve_agent_configs(
        self,
        sub_agent_configs: object,
        root_agent_config: object,
    )-> tuple[list[agent_runtime_module.AgentRuntime],
              root_agent_config_module.RootAgentConfig|None]:
        """补齐缺失的配置：入参优先，缺失项回落到加载服务。"""
        sub_configs = (
            list(sub_agent_configs)
            if self._is_config_instance(sub_agent_configs)
            and isinstance(sub_agent_configs, typing.Iterable)
            else []
        )
        root_config = root_agent_config if self._is_config_instance(root_agent_config) else None

        if sub_configs and root_config is not None:
            return sub_configs, root_config

        try:
            loaded_sub, loaded_root = self._load_agent_configs()
        except Exception:
            logger.exception("[AGENT-ORCH] load agent configs failed, continuing without configs")
            loaded_sub, loaded_root = [], None
        if not sub_configs:
            sub_configs = list(loaded_sub or ())
        if root_config is None:
            root_config = loaded_root
        return sub_configs, root_config

    @staticmethod
    def _store_agent_configs(
        app: typing.Any,
        sub_configs: typing.Sequence[typing.Any],
        root_config: typing.Any,
    )-> None:
        """把本次装配使用的配置回写到宿主。

        只在**有值**时覆盖：调用方可能只传了子 agent 配置，此时不应把已有的根
        agent 配置抹成 ``None``。
        """
        if root_config is not None:
            app.root_agent_config = root_config
        if sub_configs and isinstance(getattr(app, "sub_agents", None), list):
            app.sub_agents = list(sub_configs)

    def _ensure_agent_config_snapshots(
        self,
        *,
        sub_agents: list[agent_runtime_module.AgentRuntime]|None,
        root_agent_config: root_agent_config_module.RootAgentConfig|None,
        agent_factory: agent_factory_module.AgentFactory|None=None,
    )-> None:
        """确保宿主上存在一份「本次装配所用配置」的快照，并返回它。

        快照的用途是让后续阶段（``_explicit_runtimes_from_snapshots``、会话恢复、
        按名字重建 agent）不必重新走一遍加载链路就能拿到权威配置。

        存储位置优先宿主应用——它是跨轮次的长期对象；若宿主不接受该属性
        （``__slots__`` 之类），退回编排器实例持有，保证调用方总能拿到字典。

        快照本身是 ``{agent 名: runtime}`` 的**纯子 agent 映射**，所以根配置与
        agent 工厂不往里放（它们不是 runtime，混进去会污染「名字 → runtime」
        的语义），另存到 ``root_agent_config_snapshot`` / ``agent_factory_snapshot``。

        只在**本次有值**时覆盖：调用点允许只传部分配置，此时不应把已有快照抹空。
        """
        session_context = self.app.execution_context
        if session_context is None:
            return
        if session_context.agent_config_snapshots :
            return
        session_context.agent_config_snapshots = self._runtime_snapshots(
            sub_agents=sub_agents,
            root_agent_config=root_agent_config,
            agent_factory=agent_factory,
        )

    def _explicit_runtimes_from_snapshots(self)->list[agent_runtime_module.AgentRuntime]:
        session_context = self.app.execution_context
        snapshots = session_context.agent_config_snapshots if session_context is not None else {}
        return self._explicit_runtimes_from_snapshot_mapping(snapshots)

    def _explicit_runtimes_from_snapshot_mapping(
        self,
        snapshots: typing.Mapping[str,object],
    )->list[agent_runtime_module.AgentRuntime]:

        runtimes:list[agent_runtime_module.AgentRuntime]=[]
        for key,raw_snapshot in snapshots.items():
            snapshot = _AGENT_RUNTIME_SNAPSHOT_ADAPTER.validate_python(
                raw_snapshot,
                strict=True,
            )
            if snapshot.get("snapshot_kind") =="mode":
                continue
            snapshot_payload:dict[str,object]=dict(snapshot)
            runtime =agent_runtime_module.AgentRuntime.from_snapshot(
                snapshot_payload,
                self.app.llm_config
            )
            if not runtime.name:
                raise ValueError(f"Persisted Explicit agent snapshot has no name")
            runtimes.append(runtime)
        return runtimes

    async def _build_root_agent_for_mode(
        self,
        app_mode: str,
        role_sub_agents: typing.Sequence[agent_runtime_module.AgentRuntime]|None=None,
        utility_sub_agents: typing.Sequence[agent_runtime_module.AgentRuntime]|None=None,
        root_agent_config: root_agent_config_module.RootAgentConfig|None=None,
        agent_factory: AgentFactory|None=None,
        root_sub_agents: typing.Sequence[agent_runtime_module.AgentRuntime]|None=None,
        on_progress: typing.Optional[typing.Callable]=None,
    )-> agent_module.ReActAgent|None:
        """按应用模式构建根 agent（主 agent / 委托方）。

        根 agent 是整棵 agent 树的入口：``solo`` 模式下它独自干活，``delegate``
        模式下把任务分派给 role 型子 agent，utility 型子 agent 则更多以工具形态
        被挂载。调用点（``agent_bootstrap.execute``）拿到返回值后会读
        ``root_agent.name`` 作为默认 agent 标识，因此**返回值必须有 ``name``**。

        :param app_mode: 应用模式；为空时回落到 :meth:`_current_mode`。
        :param role_sub_agents: 角色型子 agent（``skill_type == "role"``）。
        :param utility_sub_agents: 工具型子 agent（``skill_type == "utility"``）。
        :param root_agent_config: 根 agent 配置，提供名称与系统提示词等素材。
        :param agent_factory: agent 工厂；优先使用它构建。
        :param on_process: 进度回调。
        :param root_sub_agents: ``role_sub_agents`` 的旧名（本方法桩签名的拼写），保留兼容。
        :param on_progress: ``on_process`` 的旧名，保留兼容。
        :return: 根 agent；构建不出来时返回 ``None``（由调用方转成明确的
            ``RuntimeError``，比在本层抛异常更容易定位是哪一步没产出）。

        关于取值顺序：工厂是唯一知道「怎么把配置变成 :class:`ReActAgent`」的
        一方，优先；其次是模式创建服务 / 群聊构建服务。这些服务当前都还是降级
        实现（返回空列表），所以现阶段本方法会返回 ``None``——这是**预期行为**，
        等它们补上真实创建逻辑后无需改动本方法。
        """
        app_mode = app_mode_configuration.canonicalize_app_mode(app_mode)
        registered_mode = self.app.mode_registry.get(app_mode)
        all_sub_agents = role_sub_agents + utility_sub_agents
        if registered_mode is None:
            # 可用模式清单：``ModeDeclaration`` 没有 ``.value``，模式名取 ``.name``。
            available_modes=sorted(
                declaration.name for declaration in self.app.mode_registry.available_mode_descriptors()
            )
            if app_mode in app_mode_configuration.BUILTIN_APP_MODES:
                raise ValueError(
                    f"build-n app mode: {app_mode!r}; available={available_modes}"
                )
            raise ValueError(
                f"Unknown app mode: {app_mode}; available={available_modes}"
            )
        root_agent= await self._interpret_mode(
            # ``ModelRegistry.get()`` 返回 ``RegisteredMode`` 包装对象；它已通过
            # ``__getattr__`` 把属性转发给内部声明，因此这里既可直接当
            # ``ModeDeclaration`` 用，也可用 ``.declaration`` 取原始声明。
            mode_decl= registered_mode,
            sub_agents=all_sub_agents,
            root_agent_config=root_agent_config,
            agent_factory=agent_factory,
            on_progress=on_progress,
        )
        return root_agent



    async def cleanup_session_resources(
        self,
        session_id: str | None = None,
    ) -> dict[str, pydantic.JsonValue]:
        """释放会话占用的 agent 实例与相关资源。

        退出应用必经之路（``rabbit_application.cleanup`` → ``cleanup_session_resource``
        → 本方法）。做三件事：

        1. 结束该会话的 hook 生命周期（派发 ``SessionEnd``）
        2. 释放该会话持有的 agent 实例引用
        3. 返回清理摘要，供调用方记日志

        **不抛异常**：清理阶段任何一步失败都只记日志 —— 退出路径
        不该因为清理不彻底而中断（残留资源由下次启动的恢复逻辑接管）。

        **历史缺口**：本方法此前不存在，``rabbit_application`` 调用它时
        抛 AttributeError，导致退出流程报错。
        """
        cleaned: dict[str, pydantic.JsonValue] = {
            "session_id": session_id or "",
            "released_agents": 0,
            "hook_session_ended": False,
        }

        # 1) hook 会话收尾
        try:
            lifecycle = getattr(self.app, "hook_lifecycle", None)
            if lifecycle is not None and session_id:
                from domains.hook.domain import inputs as hook_inputs

                await lifecycle.end_hook_session(
                    session_id,
                    hook_inputs.SessionEndReason.OTHER,
                )
                cleaned["hook_session_ended"] = True
        except Exception:
            logger.warning("cleanup_session_resources: hook session end failed", exc_info=True)

        # 2) 释放 agent 实例引用
        try:
            instances = getattr(self.app, "agent_instances", None)
            if isinstance(instances, dict) and session_id:
                # 只释放属于该会话的实例；无归属信息时不动 —— 误删会影响别的会话
                owned = [
                    agent_id
                    for agent_id, instance in list(instances.items())
                    if _instance_belongs_to_session(instance, session_id)
                ]
                for agent_id in owned:
                    instances.pop(agent_id, None)
                cleaned["released_agents"] = len(owned)
        except Exception:
            logger.warning("cleanup_session_resources: agent release failed", exc_info=True)

        return cleaned

    async def _interpret_mode(
        self,
        *,
        mode_decl: contracts.ModeDeclaration,
        sub_agents: typing.List[agent_runtime_module.AgentRuntime],
        root_agent_config: root_agent_config_module.RootAgentConfig,
        agent_factory: AgentFactory|None=None,
        on_progress: typing.Optional[typing.Callable]=None,
    )-> agent_module.ReActAgent:
        self.app.active_mode_declaration = mode_decl
        execution_plan = mode_orchestration.resolve_execution_plan(str(getattr(mode_decl, "name", "") or ""), mode_decl)
        try:
            # 计划挂到宿主上：TUI / CLI 可以据此展示「这个模式怎么跑」，
            # 也让「模式 -> 拓扑 -> 执行语义」三者在同一处可查。
            setattr(self.app, "active_execution_plan", execution_plan)
        except Exception:
            logger.debug("[AGENT-ORCH] cannot attach execution plan to app", exc_info=True)
        logger.info(
            "[AGENT-ORCH] mode=%s semantics=%s stages=%d workers=%s",
            execution_plan.mode,
            execution_plan.semantics,
            len(execution_plan.stages),
            list(execution_plan.worker_slots),
        )

        lazy_slot_names = {name for name, slot in mode_decl.agent_slots.items() if not slot.eager_creation}
        # 按执行计划声明的阶段顺序构建槽位：流水线 / 守护这类**顺序敏感**的模式，
        # 构建顺序与阶段顺序保持一致；计划未覆盖到的槽位（例如插件扩展出来的）
        # 仍按声明顺序追加在后，不会因为漏登记就丢失。
        ordered_slot_names = list(
            dict.fromkeys(
                [stage.slot_name for stage in execution_plan.stages]
                + [execution_plan.root_slot]
                + list(mode_decl.agent_slots)
            )
        )
        agents: dict[str, agent_module.ReActAgent] = {}
        for slot_name in ordered_slot_names:
            slot = mode_decl.agent_slots.get(slot_name)
            if slot is None or slot_name in lazy_slot_names:
                continue
            agent = await self._create_agent_for_slot(
                slot=slot,
                sub_agents=sub_agents,
                root_agent_config=root_agent_config,
                agent_factory=agent_factory,
                on_progress=on_progress,
                mode_decl=mode_decl,
            )
            agents[slot_name] = agent
        root_agent = agents.get(mode_decl.root_agent)
        if not root_agent:
            raise ValueError("root_agent is None")

        return root_agent

    @property
    def mode_orchestration(self) -> mode_orchestration.ModeOrchestrationService:
        """对外暴露的执行编排器（按模式的执行语义跑多阶段任务）。"""
        return self._mode_orchestration

    async def _create_agent_for_slot(
        self,
        *,
        slot: contracts.AgentSlot,
        sub_agents: list[agent_runtime_module.AgentRuntime],
        root_agent_config: root_agent_config_module.RootAgentConfig,
        agent_factory: AgentFactory|None=None,
        on_progress: orchestration_contracts.ProgressCallback|None=None,
        mode_decl: contracts.ModeDeclaration | None = None,
    )-> agent_module.ReActAgent:
        return await self._mode_agent_creation.create(
             self,
            slot=slot,
            sub_agents=sub_agents,
            root_agent_config=root_agent_config,
            agent_factory=agent_factory,
            on_progress=on_progress,
            mode_decl=mode_decl,
        )

    @staticmethod
    def _current_mode(app: typing.Any)-> str:
        """读取当前应用模式；读不到时按 ``delegate`` 处理。"""
        mode = getattr(app, "app_mode", None)
        if isinstance(mode, str) and mode.strip():
            return mode.strip().lower()
        return "delegate"

    def _run_bootstrap(
        self,
        app: typing.Any,
        sub_configs: typing.Sequence[typing.Any],
        root_config: typing.Any,
    )-> list[typing.Any]:
        """引导新 agent 进入运行时；失败返回空列表（不影响已创建的 agent）。"""
        try:
            return list(
                self._bootstrap.bootstrap(
                    app, agent_configs=sub_configs, root_agent_config=root_config
                )
                or ()
            )
        except Exception:
            logger.exception("[AGENT-ORCH] agent bootstrap failed")
            return []

    @staticmethod
    def _agent_id_of(agent: object)-> str|None:
        """从 agent 对象上取标识；取不到返回 ``None``（无法登记）。"""
        for attr in ("id", "agent_id", "name"):
            value = getattr(agent, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _register_created_agents(self, app: typing.Any, agents: typing.Sequence[typing.Any])-> str|None:
        """把创建出的 agent 登记到宿主容器，并确定默认 agent。

        :return: 被设为默认 agent 的标识（没有可登记对象时为 ``None``）。
        """
        runtimes = getattr(app, "agent_runtimes", None)
        first_id: str|None = None
        for agent in agents:
            agent_id = self._agent_id_of(agent)
            if agent_id is None:
                logger.debug("[AGENT-ORCH] agent without id skipped: %r", type(agent).__name__)
                continue
            if isinstance(runtimes, dict):
                runtimes[agent_id] = agent
            if first_id is None:
                first_id = agent_id

        if first_id is not None and getattr(app, "current_default_agent_id", None) is None:
            app.current_default_agent_id = first_id
        return first_id

    async def _report_progress(self, on_progress: typing.Any, *, stage: str, **payload: object)-> None:
        """向进度回调汇报装配进度。

        回调签名在调用点没有约定，这里按「关键字参数 → 单个字典 → 无参」依次尝试，
        全部失败只记日志——进度汇报不是主链路，不能因为它把装配搞挂。
        """
        if on_progress is None:
            return
        if not callable(on_progress):
            logger.warning("[AGENT-ORCH] on_process is not callable, ignored")
            return
        message = {"stage": stage, **payload}
        for call in (
            lambda: on_progress(**message),
            lambda: on_progress(message),
            lambda: on_progress(),
        ):
            try:
                result = call()
                # 协程函数要等 await 才会抛，因此等待也在保护范围内
                if inspect.isawaitable(result):
                    result = await result
            except TypeError:
                continue
            except Exception:
                logger.exception("[AGENT-ORCH] on_process failed at stage %r", stage)
                return
            return
        logger.debug("[AGENT-ORCH] on_process signature not matched at stage %r", stage)

    def _resolve_skill_agent_slot(self,
        sub_agent_config: agent_runtime_module.AgentRuntime,
    )->contracts.AgentSlot:
        if sub_agent_config.agent_slot is not None:
            return sub_agent_config.agent_slot
        registered_mode = self.app.mode_registry.get(self.app.app_mode)
        # ``RegisteredMode`` 已把属性转发给内部声明，此处可直接读声明上的策略。
        default_policy=registered_mode.default_contribution_policy if registered_mode is not None else {}
        return contracts.AgentSlot(
            name=sub_agent_config.name,
            agent_type="skill",
            contribution_policies={
                contribution_type: policy.mode_copy(deep=True) for contribution_type, policy in default_policy.items()
            },
        )

    def _resolve_root_max_tokens(
        self,
        root_agent_config: agent_runtime_module.AgentRuntime,
        agent_factory: agent_factory_module.AgentFactory,
        fallback:int=150_000,
    )->int:
        if root_agent_config.max_tokens is not None:
            return root_agent_config.max_tokens
        configured= agent_factory.config_manager.get_property("max_tokens",fallback,expected_type=int)
        if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0 :
            return configured
        return fallback

    def _resolve_visible_subagent_for_slot(
        self,
        slot: contracts.AgentSlot,
            all_sub_agents: list[agent_runtime_module.AgentRuntime],
    )->typing.List[agent_runtime_module.AgentRuntime]:
        visibility=list(slot.subagent_visibility)
        if not visibility:
            return list(all_sub_agents)
        resolved_names: list[str] = []
        for pattern in visibility:
            if pattern.startswith("*role"):
                resolved_names.extend(sa.name for sa in all_sub_agents if sa.skill_type == "role")
            if pattern.startswith("*utility"):
                resolved_names.extend(sa.name for sa in all_sub_agents if sa.skill_type == "utility")
            else:
                resolved_names.append(pattern)
            resolved_name_set= set(resolved_names)
            visible_sub_agents=[sa for sa in all_sub_agents if sa.name in resolved_name_set]
            return visible_sub_agents

    def _resolve_visible_subagent_names_for_slot(
        self,
        slot: contracts.AgentSlot,
        all_sub_agents:list[agent_runtime_module.AgentRuntime],
        model_decl: contracts.ModeDeclaration|None=None,
    )->list[str]:
        external_names=[runtime.name for runtime in self._resolve_visible_subagent_for_slot(slot, all_sub_agents)]
        declaration = model_decl or self.app.active_mode_declaration
        model_slot_names:list[str] = []
        if declaration is not None:
            for name in slot.subagent_visibility:
                if name== declaration.root_agent:
                    continue
                if name in declaration.agent_slots:
                    model_slot_names.append(name)
        # ``dict.fromkeys`` 只接受**一个**可迭代对象：这里要的是「两段名字拼接后
        # 去重且保序」，写成 ``*names`` 会在两段都为空时变成 0 个实参直接抛
        # TypeError（``fromkeys expected at least 1 argument, got 0``）。
        return list(dict.fromkeys(external_names + model_slot_names))

    async def _build_orchestrator_agent(
        self,
        agent_factory: AgentFactory,
        root_agent_config: root_agent_config_module.RootAgentConfig,
        sub_agents: list[agent_runtime_module.AgentRuntime],
        on_progress: orchestration_contracts.ProcessCallback |None=None,
        utility_tool_names: list[tuple[str,str]] |None=None,
    )->agent_module.ReActAgent:
        return await self._groupchat_agent_builder.build(
            self,
            agent_factory=agent_factory,
            root_agent_config=root_agent_config,
            sub_agents=sub_agents,
            on_progress= on_progress,
            utility_tool_names= utility_tool_names,
        )


def _instance_belongs_to_session(instance: object, session_id: str) -> bool:
    """判断一个 agent 实例是否属于指定会话。

    实例上没有直接的 ``session_id`` 字段，因此从它的上下文里找 ——
    ``Context`` 里存着会话相关的元信息。找不到归属信息时返回 ``False``：
    宁可不清���，也不要误删别的会话的实例。
    """
    context = getattr(instance, "context", None)
    if context is None:
        return False
    for holder in (context, getattr(context, "metadata", None)):
        if holder is None:
            continue
        for attr in ("session_id", "app_id", "app_instance_id"):
            value = getattr(holder, attr, None)
            if isinstance(value, str) and value == session_id:
                return True
    return False
