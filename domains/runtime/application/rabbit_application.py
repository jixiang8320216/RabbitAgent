import logging
import time
import traceback
import typing
import asyncio

import pydantic
import domains.hook.domain.inputs as hook
import domains.runtime.application.rabbit_application_composition as rabbit_application_composition
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.permission as permission
# ``confirmation`` 是历史上给 permission 域起的别名（该域最初叫 confirmation，
# 承载的是「确认/中断/授权」这一组能力）。文件内多处按 ``confirmation.`` 引用，
# 这里补上别名，避免逐处改业务代码。
import domains.permission as confirmation
import domains.runtime.application.orchestrators.agent_orchestrator as agent_orchestrator
import domains.runtime.application.orchestrators.agent_orchestration_contacts as agent_orchestration_contacts
import domains.runtime.application.orchestrators.session_orchestrator as session_orchestrator
import domains.runtime.application.orchestrators.plugin_orchestrator as plugin_orchestrator
import domains.runtime.application.role_context_service as role_context_service
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.configuration.domain.model.app_config as application_configuration
import domains.observability.domain.rabbit_observer as base_observer_module
import domains.persistence.application.persistence_plane as persistence_plane_module
import domains.persistence.domain.store_ports as persistence_ports
import domains.agent.application.lifecycle.id_generator as agent_id_generator_module
import domains.agent.domain.value_objects.message as message_module
import domains.configuration.domain.runtime_state.prompt_expansion as prompt_expansion_module
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.agent.domain.entities.agent_instance as agent_instance_module
import domains.agent.domain.entities.agent_runtime_spec as agent_runtime_spec
import domains.agent.domain.value_objects.conversation_context as context_module_2
import domains.runtime.application.agent_context_persistence as agent_context_persistence
import domains.runtime.application.app_interruption as app_interruption


logger = logging.getLogger(__name__)

class RabbitApplication(rabbit_application_composition.RabbitApplicationCompositionState):

    def __init__(
        self,
        config:application_configuration.AppConfig,
        observer:base_observer_module.RabbitObServer|None=None,
        *,
        persistence_plane: persistence_plane_module.PersistencePlane|None=None,
        session_snapshot_store:persistence_ports.SessionSnapshotStore|None=None,
        permission_resolver: permission.PermissionResolver|None=None,
        agent_id_generator:agent_id_generator_module.AgentIdGenerator|None=None,
        ) -> None:
        """
        构造 RabbitApplication。

        接收应用配置 ``app_config`` 和可选的观察者 ``observer``（TUI/CLI
        注入，用于事件回显）。两个引用存为实例属性，供后续 ``handle_user_message``
        / ``init_session`` / ``_create_agents`` 等方法使用。

        注意：基类 ``RabbitApplicationCompositionState`` 目前是空壳，未在
        ``__init__`` 里做编排初始化（session_orch / config_manager 等尚未装配），
        因此 ``handle_user_message`` 等方法被调用时仍会因 ``self._session_orch``
        缺失而报错——那是后续要补的桩，不在本次范围。
        """
        super().__init__(
            config,
            observer,
            persistence_plane =persistence_plane,
            session_snapshot_store = session_snapshot_store,
            permission_resolver = permission_resolver,
            agent_id_generator = agent_id_generator,
        )
        self._agent = agent_orchestrator.AgentOrchestrator(
            typing.cast(agent_orchestration_contacts.AgentOrchestratIONAppPort,self)
        )
        self._session_orch = session_orchestrator.SessionOrchestrator(self)
        self._plugin = plugin_orchestrator.PluginOrchestrator(
            typing.cast(plugin_orchestrator.PluginOrchestrationHost,self)
        )
        self._role_context_service = role_context_service.RoleContextApplicationService(
            role_context =self.role_context,
            agent_instances= self.agent_instances,
            app_id_provider = execution_context.get_app_id,
            default_agent_id_provider = lambda :self.current_default_agent_id,
            prompt_refresher = lambda:self.refresh_system_prompt(),
            app_state_saver=lambda app_id: self._session_orch.save_app_state_and_agents(app_id),
        )

    def _load_agent_configs(
        self,
        selected_expert_ids: typing.Sequence[str]|None=None,
        selectec_skill_names:typing.Sequence[str]|None=None,
    )-> typing.Tuple[typing.List,typing.Optional[root_agent_config_module.RootAgentConfig]|None]:
        return self._agent._load_agent_configs(
            selected_expert_ids=selected_expert_ids,
            selectec_skill_names=selectec_skill_names,
        )

    async def interrupt_turn(
        self,
        agent_id:str,
        reason:str="user_interrupt",
        )->dict[str, pydantic.JsonValue]:
        return await self._interruption_coordinator().interrupt_background_agent(
            agent_id,
            reason=reason,
        )
    async def interrupt_background_agent(
        self,
        agent_id:str,
        reason:str="user_stop_agent",
    )->dict[str, pydantic.JsonValue]:
        return await self._interruption_coordinator().interrupt_background_agent(
            agent_id,
            reason=reason,
        )

    def interrupt_turn_sync(self, reason: str="sigint_ctrl_c")->str:
        now = time.time()
        cls =RabbitApplication
        if now -cls._sigint_last_ts<2.0:
            cls._sigint_count+=1
        else:
            cls._sigint_count=1
        cls._sigint_last_ts=now

        if cls._sigint_count >=2:
            return "FORCE_EXIT"

        cls._pending_signal_interrupts.append(reason)

        try:
            loop= asyncio.get_running_loop()
        except RuntimeError:
            loop=None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._schedule_drain,loop)
            except Exception:
                logger.error(traceback.format_exc())
        return "INTERRUPT"

    async def close_session(
        self,
        reason:str ="session_close",
        *,
        session_id:str|None=None,
    )->dict[str, pydantic.JsonValue]:
        start= time.time()
        session_id = session_id or self._resolve_session_id() or "__unknown__"
        if session_id !="__unknown__":
            await self.end_hook_session(session_id, hook.SessionEndReason.OTHER)
        sess = confirmation.InterruptManager.get_session_scope(session_id)
        sess.cancel(reason=reason)

        topic_tasks_cancelled = await self._session_orch.cancel_topic_generation()
        killed =0
        try:
            killed = await confirmation.InterruptManager.kill_session_processes(session_id)
        except Exception as e:
            logger.debug(e)
        return {
            "scope_cancelles":1,
            "topic_tasks_cancelled": topic_tasks_cancelled,
            "process_killed":killed,
            "duration_ms": int((time.time() - start) * 1000),
            "reason" : reason,
        }

    async def cleanup(
        self,
        reason: hook.SessionEndReason = hook.SessionEndReason.PROMPT_INPUT_EXIT,
    ) ->dict[str, pydantic.JsonValue]:
        session_id = self._reslove_session_id() or self.session_manager.current_session_id
        if session_id:
            try:
                await self.end_hook_session(session_id, reason)
            except Exception as e:
                logger.error(e)
        await self.dispose_hook_resources()
        return await self.cleanup_session_resource()

    async def cleanup_session_resource(
        self,
        session_id: str|None=None,
    )->dict[str, pydantic.JsonValue]:
        return await self._agent.cleanup_session_resources(session_id)

    async def persist_agent_context_strict(
        self,
        agent_id:str,
        expected_context: context_module_2.ContextState
    )->None:
        store = self._agent_instance_store()
        if store is None:
            raise RuntimeError("strict Context persistence")
        service = agent_context_persistence.AgentContextPersistenceService(
            app_id= self._required_durable_app_id(),
            store=store,
            instance=self.agent_instances,
            locks=self._agent_instance_write_locks,
        )
        await service.persist(agent_id, expected_context)

    def get_agent_config(self):
        """``_load_agent_configs`` 的别名，CLI 路径 ``application.py:115`` 用此名。"""
        return self._load_agent_configs()

    async def ensure_plugins_initialized(self)->None:
        return await self._plugin.ensure_initialized()

    async def load_agent_instance(self, agent_id:str)-> agent_instance_module.AgentInstance:
        instance =self.agent_instances.get(agent_id)
        if instance is not None:
            return instance
        lock=self._agent_instance_load_locks.setdefault(agent_id,asyncio.Lock())
        async with lock:
            instance= self.agent_instances.get(agent_id)
            if instance is not None:
                return instance
            store= self._agent_instance_store()
            if store is None:
                return None
            instance = await store.load(self._required_durable_app_id(),agent_id)
            if instance is not None:
                self.agent_instances[agent_id] = instance
            return instance

    async def _create_agents(
        self,
        sub_agent_configs=None,
        root_agent_config=root_agent_config_module.RootAgentConfig,
        on_progress=None,
        *,
        defer_root_creation:bool=False,
    )->None:
        """根据配置创建 Plan Agent + Sub-Agents。骨架：返回 None。"""
        return await self._agent._create_agents(
            sub_agent_configs=sub_agent_configs,
            root_agent_config=root_agent_config,
            on_progress=on_progress,
            defer_root_creation=defer_root_creation,
        )


    def build_agent_for_invoke(
        self,
        agent_id: str,
    ) -> agent_module.ReActAgent:
        """按标识取一个**可调用**的 agent，供一轮对话入口使用。
        """
        if not agent_id:
            raise RuntimeError("build_agent_for_invoke: agent_id is None")
        if agent_id not in self.agent_instances:
           raise RuntimeError(r"build agent_for_invoke: agent_id is unkown")

        activated=self._current_invoke_agent
        if activated is None:
            raise RuntimeError(r"build_agent_for_invoke: activated is None")
        if activated.name != agent_id:
            raise RuntimeError(r"build_agent_for_invoke: activated name is unkown")
        return activated

    def register_agent_runtime(
        self,
        agent_id: str,
        runtime: agent_module.ReActAgent,
    ) -> None:
        """登记一个已构造好的 agent 运行时。

        必须**同时写两张表**，只写一张都会留下断点：

        * ``agent_runtimes``：按名字保存运行时本体。``mode_agent_creation``
          的 ``_unique_agent_name`` 靠它判重（重名加序号而不是静默覆盖），
          ``_register`` 也只写这一张表。
        * ``agent_instances``：``build_agent_for_invoke`` 用
          ``agent_id in self.agent_instances`` 做存在性校验。只写 runtimes
          会让它必抛 ``agent_id is unkown``——这是本方法存在的主因。

        另外 ``build_agent_for_invoke`` 还会校验 ``activated.name == agent_id``，
        所以 ``agent.name`` 也必须可查；与传入的 ``name`` 不同时两个键都登记。

        两张表的 value 都直接放 agent 本体：``AgentInstance`` 类型目前没有
        实现，而已有的消费点只用到「键是否存在」和 ``dict.get()``。

        :param name: 登记标识；为空时回退到 ``agent.name``。
        :param agent: agent 运行时本体；``None`` 时忽略。
        """
        instance= self.agent_instances.get(agent_id)
        if instance is None or instance.spec.extra.get("compatibility_placeholder",False):
            raise RuntimeError(r"register_agent_runtime: agent_id is unkown")
        if runtime.name != agent_id:
            raise RuntimeError(r"register_agent_runtime: agent_id is unkown")
        if runtime.context is not instance.context:
            raise RuntimeError(r"register_agent_runtime: agent_id is unkown")
        self.agent_runtimes[agent_id] = runtime

    async def init_session(self, session_id: str | None = None, *, is_resume: bool = False)->str:
        """初始化会话。骨架：返回传入的 session_id。"""
        return await self._session_orch.init_session(session_id=session_id, is_resume=is_resume)

    @property
    def root_agent(self)-> typing.Optional[agent_module.ReActAgent]:
        return self._current_invoke_agent

    @root_agent.setter
    def root_agent(self,agent: typing.Optional[agent_module.ReActAgent])->None:
        if agent is None:
            self._current_invoke_agent = None
            return
        self.register_agent_runtime(agent.name,agent)
        self.agent_tree.register_root(agent)
        self._current_invoke_agent = agent
        logger.debug(f"agent {agent.name} registered")

    def bind_agent_context(self, agent_id:str, agent_context: context_module_2.Context)->None:
        self.contexts[agent_id] = agent_context

    def bind_agent_identity(
        self,
        agent_id: str,
        agent_spec: agent_runtime_spec.AgentRuntimeSpec,
        agent_context: context_module_2.Context,
        ):
        if agent_spec.agent_id != agent_id:
            raise RuntimeError(r"bind_agent_identity: agent_id is unkown")
        if agent_spec.extra.get("compatibility_placeholder",False):
            raise RuntimeError(r"bind_agent_identity: agent_id is unkown")
        self.bind_agent_spec(agent_id,agent_spec)
        self.bind_agent_context(agent_id,agent_context)

    def bind_agent_spec(self,
        agent_id:str,
        agent_spec:agent_runtime_spec.AgentRuntimeSpec,
    ):
        """把 spec 登记进 ``agent_instances``。

        这里**必须**存 ``AgentInstance`` 而不是裸 spec：``contexts[agent_id] =
        ...`` / ``agent_configs[agent_id] = ...`` 两个视图都是按
        ``AgentInstance`` 的 ``.context`` / ``.spec`` 属性读写的。直接塞 spec
        会让下一次写上下文撞上 ``no_such_attribute: context``。
        """
        existing = self.agent_instances.get(agent_id)
        if existing is None:
            self.agent_instances[agent_id]= agent_instance_module.AgentInstance(
                agent_id=agent_id,
                spec=agent_spec,
            )
            return
        if isinstance(existing, agent_instance_module.AgentInstance):
            try:
                existing.spec = agent_spec
                return
            except Exception:  # noqa: BLE001 - 校验失败就整体替换，保住不丢 spec
                logger.debug("bind_agent_spec: 就地更新失败，改为替换实例", exc_info=True)
        # 已有的是占位实例或不可就地更新：保留它的 context，换一个新实例。
        context_value = getattr(existing, "context", None)
        try:
            self.agent_instances[agent_id] = agent_instance_module.AgentInstance(
                agent_id=agent_id,
                spec=agent_spec,
                context=context_value if context_value is not None else context_module_2.Context(),
            )
        except Exception:  # noqa: BLE001
            self.agent_instances[agent_id] = agent_instance_module.AgentInstance(
                agent_id=agent_id,
                spec=agent_spec,
            )

    async def handle_user_message(
        self,
        user_msg: message_module.Msg,
        session_id:str,
        *,
        prompt_expansion: prompt_expansion_module.PromptExpansion|None = None,
        **kwargs:object,
    ) -> message_module.Msg:
        if prompt_expansion is not None:
            kwargs["prompt_expansion"] = prompt_expansion
        return await self._session_orch.handle_user_message(
                user_msg,
                session_id,
                **kwargs,
            )

    # --- 中断协调 ------------------------------------------------------------

    def _interruption_coordinator(self) -> app_interruption.AppInterruptionCoordinator:
         return app_interruption.AppInterruptionCoordinator(
             self,
             session_id_resolver=self._resolve_session_id,
             turn_interrupter =self.interrupt_turn(),
             turn_locks = self._turn_interrupt_locks,
             turn_locks_guard= self._turn_interrupt_locks_guard,
             in_flight_interruptes = self._in_flight_interrupts,
         )

    def _resolve_session_id(self) -> typing.Optional[str] :
        try:
            return execution_context.get_session_id()
        except Exception as exc:  # noqa: BLE001 - 查询失败按「没有会话」处理
            logger.debug("resolve session id failed: %s", exc)
            return None

    # 历史拼写别名：``cleanup`` 调的是 ``_reslove_session_id``（i/o 颠倒）。
    # 与仓内 ``rety_cause`` / ``has_existin_display_topic`` 等做法一致——
    # 两种拼写并存，正确拼写为实现本体。
    _reslove_session_id = _resolve_session_id

    async def end_hook_session(self,
        session_id: str,
        reason: hook.SessionEndReason,
        *,
        cwd:str|None = None,
    ) -> hook.HookDispatchResult|None:
        """派发 ``SessionEnd`` hook。

        修改前委派给 ``self._session_orch.end_hook_session``，但
        ``SessionOrchestrator`` 上**并没有**这个方法——``end_hook_session``
        定义在 ``SessionHookLifecycleCoordinator`` 上，只有
        ``_session_hook_lifecycle_coordinator()`` 才会构造它。于是每次会话收尾
        （``cleanup`` / ``cleanup_session_resource``，也就是**退出应用必经之路**）
        都抛 ``AttributeError: 'SessionOrchestrator' object has no attribute
        'end_hook_session'``，异常一路冒到 Textual 的 run_async，TUI 以
        ``return_code=1`` 异常终止。

        改为委派给真正持有实现的 ``SessionHookLifecycleCoordinator``。
        """
        coordinator = self._session_orch._session_hook_lifecycle_coordinator()
        return await coordinator.end_hook_session(session_id, reason, cwd=cwd)

    async def dispose_hook_resources(self, timeout_seconds: float=60.0) -> None:
        try:
            await self._hook_resume_service.close()
        except Exception:
            logger.warning("dispose_hook_resources failed", exc_info=True)
        try:
            await self.plugin_manager.shutdown_plugins(timeout=timeout_seconds)
        except Exception:
            logger.warning("dispose_hook_resources failed", exc_info=True)
        lifecycle = self.hook_lifecycle
        if lifecycle is None:
            return
        try:
            await lifecycle.stop_all_watching()
        except Exception:
            logger.warning("dispose_hook_resources failed", exc_info=True)
        try:
            await lifecycle.drain_background(timeout_seconds)
        except Exception:
            logger.warning("dispose_hook_resources failed", exc_info=True)

    def _agent_instance_store(self) -> typing.Any:
        persistence_plane= self.persistence_plane
        if persistence_plane is None or persistence_plane.agent_instance is None:
            return None
        return persistence_plane.agent_instance


    def _required_durable_app_id(self) -> str:
        app_id = self._durable_app_id or execution_context.get_app_id()
        if not isinstance(app_id, str) or not app_id.strip():
            raise RuntimeError("Lazy AgentInstance loading requires a bound durable app id")
        return app_id

    async def request_pending_hook_rewake(self, session_id: str) -> bool:
        if self.hook_lifecycle is None:
            return False
        deliveries= await self.hook_lifecycle.peek_pending_async_deliveries(session_id)
        delivery= next((item for item in deliveries if item.rewake), None)
        if delivery is None:
            return False
        await self.request_pending_hook_rewake(
            session_id,
            delivery.hook_id,
            delivery.rewake_message or "Background Hook requested a rewake"
        )
        return True


class _InterruptionCoordinator:
    """后台 agent 中断协调器（最小实现）。

    ``RabbitApplication.interrupt_turn`` / ``interrupt_background_agent`` 经
    ``_interruption_coordinator()`` 拿到它。真身（按 agent_id 精确定位到
    进程/任务的中断）尚未落地；当前实现只做确定成立的三件事：

    1. 解析 agent 所属会话（宿主的执行上下文）；
    2. 给该会话打一次性中断标记（``InterruptManager.request_interrupt``）——
       ReAct 循环与工具执行前的检查点会消费它；
    3. 取消该会话当前轮次的作用域（``get_turn_scope().cancel()``）——正在进行
       的模型调用/工具执行立刻收到 ``CancelledError``。

    ``agent_id`` 目前只进摘要（agent→会话 的精确映射随实例存储一起落地），
    不参与判定——一个 RabbitApplication 对一个会话，中断粒度到会话即可。
    """

    def __init__(self, app: "RabbitApplication") -> None:
        self._app = app

    async def interrupt_background_agent(
        self,
        agent_id: str,
        *,
        reason: str = "user_stop_agent",
    ) -> dict[str, pydantic.JsonValue]:
        summary: dict[str, pydantic.JsonValue] = {
            "agent_id": str(agent_id),
            "reason": reason,
            "session_id": None,
            "interrupt_requested": False,
            "turn_scope_cancelled": False,
        }
        session_id = self._app._resolve_session_id()
        summary["session_id"] = session_id
        if not session_id:
            logger.debug("interrupt %r without session id; nothing to interrupt", agent_id)
            return summary
        try:
            confirmation.InterruptManager.request_interrupt(session_id)
            summary["interrupt_requested"] = True
        except Exception as exc:  # noqa: BLE001 - 中断是尽力而为
            logger.debug("request interrupt failed: %s", exc)
        scope = confirmation.InterruptManager.get_turn_scope(session_id)
        if scope is not None and callable(getattr(scope, "cancel", None)):
            try:
                scope.cancel(reason=reason)
                summary["turn_scope_cancelled"] = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("cancel turn scope failed: %s", exc)
        return summary
        