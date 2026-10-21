from __future__ import annotations
import asyncio
import typing
import logging
import domains.runtime.application.orchestrators.session_topic as session_topic
import domains.runtime.application.orchestrators.session_state_writer as session_state_writer
import domains.runtime.application.orchestrators.session_state_hydrator as session_state_hydrator
import domains.runtime.application.orchestrators.session_hook_turns as session_hook_turns
import domains.runtime.application.orchestrators.session_lifecyle as session_lifecyle
import domains.runtime.application.orchestrators.session_turn_checkpoint as session_turn_checkpoint
import domains.runtime.application.orchestrators.session_turn_execution as session_turn_execution
import domains.runtime.application.orchestrators.session_incremental_save as session_incremental_save
import domains.runtime.application.orchestrators.session_hook_lifecycle as session_hook_lifecycle
import domains.plugin.application.request_context as context_module
import domains.agent.domain.value_objects.message as message_module
import domains.agent.domain.aggregates.react_agent as agent
import domains.persistence.domain.conversation_branch as branch_domain
import domains.configuration.domain.runtime_state.turn_completion as turn_completion_module
import domains.configuration.domain.runtime_state.session_models as session_models
import domains.agent.domain.entities.input_overflow_result as input_overflow_result
import domains.agent.domain.value_objects.runtime_context as process_runtime_context
import datetime as datetime_module



if typing.TYPE_CHECKING:
    # 只用于类型注解。运行时导入会与 ``rabbit_application`` 形成环：
    # ``rabbit_application.py`` 在模块级 import 了本模块来构造 SessionOrchestrator。
    # 放进 TYPE_CHECKING 后，因为开了 ``from __future__ import annotations``，
    # 注解保持字符串不求值，环就断了。
    import domains.runtime.application.rabbit_application as rabbit_application

logger = logging.getLogger(__name__)

_project_runtime_plan = session_state_writer.project_runtime_plan
get_runtime_context= process_runtime_context.get_runtime_context
####整个是原始函数
class SessionOrchestrator:
    """会话级编排器。"""

    def __init__(self, app:rabbit_application.RabbitApplication) -> None:
        """:param app: 宿主应用（``RabbitApplication`` 自身或兼容对象）。"""
        self.app = app
        self._topic_coordinator =session_topic.SessionTopicCoordinator(app)
        self._topic_generation_tasks =self._topic_coordinator.tasks
        self._state_writer = session_state_writer.SessionStateWriter(app)
        self._state_hydrator = session_state_hydrator.SessionStateHydrator(app)
        self._hook_turns= session_hook_turns.SessionHookTurnCoordinator(app)
        self._session_lifecycle = session_lifecyle.SessionLifecycleCoordinator(app,self)
        self._turn_checkpoint = session_turn_checkpoint.SessionTurnCheckpointCoordinator(app)
        self._turn_executor_instance = session_turn_execution.SessionTurnExecutor(app, self)
        self._app_state_save_lock=asyncio.Lock()
        self._incremental_save= session_incremental_save.SessionIncrementalSaveCoordinator(self._save_session)

    def _bind_plugin_request_context(self)-> None:
        context_module.set_plugin_manager(self.app.plugin_manager)
        declaration =self.app.active_mode_declaration
        root_slot = declaration.agent_slots.get(declaration.root_agent) if declaration is not None else None
        context_module.set_current_slot(root_slot)

    @property
    def project_name(self)->typing.Optional[str]:
        return self.app.user_project_home

    def _turn_hook_coordinator(self) ->  session_hook_turns.SessionHookTurnCoordinator:
        try:
             return self._hook_turns
        except AttributeError:
            self._hook_turns = session_hook_turns.SessionHookTurnCoordinator(self.app)
            return self._hook_turns

    def _session_lifecycle_coordinator(self)->session_lifecyle.SessionLifecycleCoordinator:
        try:
             return self._session_lifecycle
        except AttributeError:
            self._session_lifecycle = session_lifecyle.SessionLifecycleCoordinator(self.app)
            return self._session_lifecycle

    def _turn_executor(self) -> session_turn_execution.SessionTurnExecutor:
        try:
            return self._turn_executor_instance
        except AttributeError:
            self._turn_executor_instance = session_turn_execution.SessionTurnExecutor(self.app)
            return self._turn_executor_instance

    def _turn_checkpoint_coordinator(self) -> session_turn_checkpoint.SessionTurnCheckpointCoordinator:
        try:
            return self._turn_checkpoint
        except AttributeError:
            self._hook_turns = session_turn_checkpoint.SessionTurnCheckpointCoordinator(self.app)
            return self._session_lifecycle

    def _session_hook_lifecycle_coordinator(self)->session_hook_lifecycle.SessionHookLifecycleCoordinator:
        return session_hook_lifecycle.SessionHookLifecycleCoordinator(
            self.app,
            metadata_factory=self._hook_metadata,
            save_app_state=self.save_app_state_and_agents,
        )

    def _hook_metadata(
        self,
        session_id: str,
        *,
        correlation_id:str,
        prompt_id:str|None=None,
        entry_agent: agent.ReActAgent|None=None,
        model_index:int|None=None,
        agent_id: str|None=None,
        agent_type:str |None=None,
    ):
        return self._turn_hook_coordinator()._hook_metadata(
            session_id,
            correlation_id=correlation_id,
            prompt_id=prompt_id,
            entry_agent=entry_agent,
            model_index=model_index,
            agent_id=agent_id,
            agent_type=agent_type,
        )



    @staticmethod
    def _check_input_overflow(
        user_msg: message_module.Msg,
    ) -> tuple[message_module.Msg, input_overflow_result.InputOverflowResult]:
        return session_turn_execution.SessionTurnExecutor.check_input_overflow(user_msg)


    async def handle_user_message(
        self,
        user_msg: message_module.Msg,
        session_id: str,
        **kwargs: object
    )-> message_module.Msg:
        return await self._turn_executor().handle_user_message(user_msg, session_id, **kwargs)

    # # --- 会话初始化 ---------------------------------------------------------
    #
    async def init_session(self, session_id: str | None = None, *, is_resume: bool = False) -> str:
        from domains.session.infrastructure.session_persistence import SessionPersistence
        return await self._session_lifecycle_coordinator().init_session(
            session_id,
            is_resume,
            persistence_factory=SessionPersistence,
            runtime_context_provider= get_runtime_context,
        )

    async def _publish_turn_checkpoint(self, session_id: str,
                                       *,
                                       turn_id:str,
                                       boundary: branch_domain.CheckpointBoundary,
                                       user_msg: message_module.Msg|None = None,
                                       ) -> None:
        """发布轮次检查点（用于工作区版本/回滚）。未接线时不做任何事。"""
        await self._turn_checkpoint_coordinator().publish_turn_checkpoint(session_id,
                                                                          turn_id=turn_id,
                                                                          boundary=boundary,
                                                                          user_msg=user_msg)

    def _prepare_turn_identity(self, user_msg: message_module.Msg, session_id: str) -> str:
        return session_turn_checkpoint.SessionTurnCheckpointCoordinator._prepare_turn_identity(user_msg, session_id)

    async def _run_prompt_hooks(
        self,
        *,
        entry_agent: typing.Any,
        user_msg: typing.Any,
        session_id: str,
        expansion: typing.Any = None,
        model_index: typing.Any = None,
    ) -> tuple[typing.Any, typing.Any, list[str]]:
        """跑前置 prompt hook。"""
        return await self._turn_hook_coordinator()._run_prompt_hooks(
            entry_agent=entry_agent,
            user_msg=user_msg,
            session_id=session_id,
            expansion=expansion,
            model_index=model_index,
        )

    async def save_app_state_and_agents(self,
                                        app_id: str,
                                        *,
                                        preserve_updated_at: bool=False,
                                        turn_completion_witness: turn_completion_module.TurnCompletionWitness|None = None,
                                        turn_admission_agent_id: str|None = None,
                                        ) -> None:
        """保存应用状态与 agent 快照。未接线时不做任何事。"""
        async with self._app_state_save_lock:
            await self._save_app_state_and_agents_unlocked(
                app_id,
                preserve_updated_at=preserve_updated_at,
                turn_completion_witness=turn_completion_witness,
                turn_admission_agent_id=turn_admission_agent_id,
            )


    async def _run_turn_completion_hooks(
        self,
        entry_agent: agent.ReactAgent,
        response: message_module.Msg,
        session_id: str,
        *,
        model_index: int | None = None,
    ) -> message_module.Msg:
       # model_index 在 SessionHookTurnCoordinator._run_turn_completion_hooks 里是
       # keyword-only 参数（签名带独立的 "*"）。原写法把它当第 4 个位置参数传入，
       # 会抛 "TypeError: _run_turn_completion_hooks() takes 4 positional arguments
       # but 5 were given"，使每轮对话在拿到答案后失败。
       return await self._turn_hook_coordinator()._run_turn_completion_hooks(
           entry_agent,
           response,
           session_id,
           model_index=model_index,
       )

    async def _run_stop_failure_hook(
        self,
        *,
        entry_agent: typing.Any,
        session_id: str,
        error: BaseException,
        model_index: int | None = None,
    ) -> None:
        """轮次失败后的收尾 hook（通知界面、尽力保存现场）。

        ``session_turn_execution._handle_turn_failure`` 会调用本方法；此前它不
        存在，导致每次 turn 失败都以 ``'SessionOrchestrator' object has no
        attribute '_run_stop_failure_hook'`` 收场——错误既没进界面也没进日志。

        协调器（``SessionHookTurnCoordinator``）目前没有对应的失败 hook，所以
        这里按「有就委托，没有就安全跳过」处理：失败收尾是尽力而为的动作，
        缺口不该再抛回已经处于异常分支的调用方。
        """
        coordinator = self._turn_hook_coordinator()
        hook = getattr(coordinator, "_run_stop_failure_hook", None)
        if hook is None:
            logger.debug(
                "stop failure hook unavailable on %s; skipped for session %s",
                type(coordinator).__name__,
                session_id,
            )
            return
        await hook(
            entry_agent=entry_agent,
            session_id=session_id,
            error=error,
            model_index=model_index,
        )

    async def _save_session(
        self,
        session_id: str,
        *,
        turn_completion_witness: turn_completion_module.TurnCompletionWitness|None = None,
        turn_admission_agent_id: str | None = None,
        preserve_updated_at: bool = False,
        **kwargs: object,
    ) -> None:
        """持久化会话状态。

        **必须是协程**：``_save_completed_turn`` 用 ``asyncio.create_task(...)`` 包住
        本调用，再以 ``asyncio.shield`` 等待，从而把「保存期间的取消」推迟到上下文
        持久化回调跑完之后。如果这里写成同步函数，``create_task`` 会直接 TypeError。

        参数语义：

        - ``turn_completion_witness``：轮次完成事实记录，落盘后会话才能被正确恢复；
        - ``turn_admission_agent_id``： admission 阶段写入（用户消息刚进上下文）；
        - ``preserve_updated_at``：``True`` 表示不要刷新会话的 ``updated_at``——
          纯技术性写入不该让会话在列表里"跳到最新"。

        这里**不吞异常**：调用方需要靠异常来决定后续行为（``_persist_admitted_context``
        会把失败转成 ``TurnContextPersistenceIndeterminateError``，``_save_completed_turn``
        会区分取消与普通失败）。同理，``asyncio.CancelledError`` 绝不吞。
        """
        if not self.app.session_persistence:
            if (
                turn_completion_witness is not None
                or turn_admission_agent_id is not None
            ):
                raise RuntimeError("Cannot persist admitted context")
            return
        existing_ctx= self.app.execution_context
        current_plan= self.app.plan_runtime.current_plan if self.app.plan_runtime  is not None else None
        selected_expert_ids = session_state_writer.project_group_chat_selected_expert_ids(self.app)
        self.app.execution_context = session_models.ExecutionContext(
            session_id=session_id,
            created_at = existing_ctx.created_at if existing_ctx else datetime_module.datetime.now().isoformat(),
            last_updated= datetime_module.datetime.now().isoformat(),
            working_directory= str(self.app.user_project_home),
            app_mode = self.app.app_mode,
            current_plan =_project_runtime_plan(current_plan) ,
            historical_plans=[],
            group_chat_selected_expert_ids=selected_expert_ids,
            group_chat_selected_skills = None,
            agent_config_id=(
                existing_ctx.agent_config_id
                if existing_ctx
                else None
            ),
        )
        role_selection=(
            existing_ctx.role_selection
            if existing_ctx
            else None
        )
        agent_config_snapshots=(
            dict(existing_ctx.agent_config_snapshots or {})
            if existing_ctx
            else {}
        )

        await self.save_app_state_and_agents(
            session_id,
            preserve_updated_at=preserve_updated_at,
            turn_completion_witness = turn_completion_witness,
            turn_admission_agent_id = turn_admission_agent_id,
        )

    def _notify_user_turn_finalized(self, session_id: str) -> None:
         self._turn_executor().notify_user_turn_finalized(session_id)

    async def _save_app_state_and_agents_unlocked(
        self,
        app_id: str,
        *,
        preserve_updated_at: bool = False,
        turn_completion_witness: turn_completion_module.TurnCompletionWitness | None = None,
        turn_admission_agent_id: str | None = None,
    ) -> None:
        await self._state_writer.save(
            app_id,
            preserve_updated_at=preserve_updated_at,
            turn_completion_witness=turn_completion_witness,
            turn_admission_agent_id=turn_admission_agent_id,
        )

    def _apply_model_override(
            self,
            entry_agent: agent.ReactAgent,
            model_index: int,
    ) -> agent.ReactAgent:
        return self._turn_executor().apply_model_override(entry_agent, model_index)

    async def _run_stop_failure_hook(
        self,
        *,
        entry_agent: typing.Any = None,
        session_id: str,
        error: BaseException,
        model_index: int | None = None,
    ) -> None:
        """turn 失败收尾钩子（补齐缺失实现）。

        调用方 ``session_turn_execution._handle_turn_failure`` 在 ``except`` 分支里
        用 ``await self.owner._run_stop_failure_hook(...)`` 调用本方法，且外层自己包了
        try/except，因此本方法即便抛错也只会被记成一条 warning——不会再盖掉真正根因。

        这里尽力做两件事：

        1. 把错误事实记到日志（排查真正根因的关键线索）；
        2. 若宿主应用暴露了 ``on_turn_error`` 通知通道，就把失败转达给界面，否则
           界面只会看到 spinner 一直转、看不到任何错误提示。

        绝不抛异常——它是失败处理的最后一环。
        """
        logger.warning(
            "[TURN] stop failure hook: session=%s error=%s: %s",
            session_id,
            type(error).__name__,
            error,
        )
        notifier = getattr(self.app, "on_turn_error", None)
        if callable(notifier):
            try:
                result = notifier(session_id=session_id, error=error)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.debug("[TURN] on_turn_error notifier raised: %s", exc)



