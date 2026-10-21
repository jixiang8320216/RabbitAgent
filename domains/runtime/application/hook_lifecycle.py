
from __future__ import annotations
import collections
import asyncio
import hashlib
import logging
import pathlib
import uuid
import typing
import os

import pydantic

import domains.hook.domain.hook_definitions as hook_models
import domains.runtime.application.hook_event_sink as hook_event_sink
import domains.hook.application.store_ports as hook_store_ports
import domains.persistence.domain.store_ports as persistence_ports
import domains.runtime.application.hook_lifecycle_contracts as hook_lifecycle_contracts
import domains.hook.application.runtime_ports as hook_runtime_ports
import domains.hook.application.run_recorder as hook_run_recorder
import domains.hook.infrastructure.handler_execution as hook_handler_execution
import domains.hook.domain.resource_models as hook_resource_models
import domains.hook.domain.runtime_models as hook_runtime_models
import domains.hook.domain.inputs as hook_inputs
import domains.hook.domain.outputs as hook_outputs
import domains.hook.application.resource_ports as hook_resource_ports
import domains.hook.application.compiler as hook_compiler
import domains.hook.application.state_service as hook_state_service
import domains.hook.application.hook_runtime as hook_runtime
import domains.hook.application.hook_transcript as hook_transcript
import domains.hook.application.async_delivery as async_delivery
import domains.persistence.infrastructure.file.file_hook_stores as file_hook_stores
import domains.hook.domain.store_models as hook_store_models
import domains.hook.application.recursion as hook_recursion
import domains.hook.domain.env_file as hook_env_file
import domains.session.infrastructure.session_id_safety as session_id_safety
import domains.runtime.application.hook_file_watch_coordination as hook_file_watch_coordination
class _FallbackHookLifecycleMetadata:
    """``hook_lifecycle_contracts`` 尚未落地（当前为空模块）时的同型占位。

    模块顶部那行赋值若直接取属性会 ``AttributeError``，而本模块被
    ``runtime/service/react_agent.py`` 在 import 期引用——一个空模块就会让整个
    进程起不来。这里给出同型替身，等 ``hook_lifecycle_contracts`` 真正实现了
    ``HookLifecycleMetadata``，下面的 ``try`` 会优先取真身，替身自动让位。

    字段与 ``build_hook_lifecycle_metadata`` 的调用点一一对应，全部可省略。
    """

    __slots__ = (
        "session_id",
        "correlation_id",
        "prompt_id",
        "entry_agent",
        "model_index",
        "agent_id",
        "agent_type",
        "extra",
    )

    def __init__(
        self,
        session_id: str = "",
        *,
        correlation_id: str = "",
        prompt_id: str | None = None,
        entry_agent: object | None = None,
        model_index: int | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
        **extra: object,
    ) -> None:
        self.session_id = session_id
        self.correlation_id = correlation_id
        self.prompt_id = prompt_id
        self.entry_agent = entry_agent
        self.model_index = model_index
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.extra = dict(extra)

    def __repr__(self) -> str:
        return (
            f"HookLifecycleMetadata(session_id={self.session_id!r}, "
            f"agent_id={self.agent_id!r}, agent_type={self.agent_type!r})"
        )


try:
    HookLifecycleMetadata = hook_lifecycle_contracts.HookLifecycleMetadata
except AttributeError:  # contracts 为空模块：用上面的同型占位，避免 import 期崩溃
    HookLifecycleMetadata = _FallbackHookLifecycleMetadata

_WATCHER_GRACEFUL_STOP_SECONDS=2.0

logger = logging.getLogger("hook-lifecycle")


class _DegradedDispatchResult:
    """``HookDispatchResult`` 尚未落地时的同型替身。

    与 :class:`_FallbackHookLifecycleMetadata` 同理：``runtime_models`` 当前是
    空模块，而 ``_dispatch`` 在 hook 基础设施未就绪时必须给调用方一个形状完整
    的返回值。这里只提供「放行」语义所需的那几个字段，够用即可。
    """

    __slots__ = ("event", "action", "continue_processing", "additional_context", "reason")

    def __init__(
        self,
        event: object | None = None,
        action: object | None = None,
        continue_processing: bool = True,
        additional_context: str = "",
        reason: str = "",
    ) -> None:
        self.event = event
        self.action = action
        self.continue_processing = continue_processing
        self.additional_context = additional_context
        self.reason = reason

    def __repr__(self) -> str:
        return (
            f"_DegradedDispatchResult(event={self.event!r}, "
            f"continue_processing={self.continue_processing!r})"
        )

class HookLifecycleCoordinator:
    """配置 hook 的执行与生命周期协调。

    构造参数与 ``RabbitApplicationCompositionState`` 的装配调用一一对应；
    除 ``snapshot`` / ``policy`` 外全部可省略，省略即「该能力未就绪」，运行时会
    自动降级（不抛异常、不阻断主流程）。

    :param snapshot: 初始配置快照（``HookSnapshot`` / ``{事件: hook...}`` / ``None``）。
    :param policy: 执行策略（``HookPolicy``）；``None`` 取默认值。
    :param project_dir / rabbit_data_dir / apps_dir: 运行期目录。
    :param state_store / run_store / event_store: 持久化句柄（``PersistencePlane``）。
    :param observer: 运行期 observer，用 ``on_hook_event`` 上报。
    :param mcp_client / model_client: hook 运行期可用的 MCP / 模型客户端适配器。
    :param configuration_paths / configuration_fingerprint / initial_diagnostics:
        配置来源追踪信息（诊断「这条 hook 从哪来」）。
    :param configuration_refresher: 配置刷新器（``refresh()`` 时调用）。
    :param component_context_updater: 组件级上下文更新器。
    :param file_watcher: 配置文件监听器（``dispose()`` 时停止）。
    :param async_rewake_scheduler: 异步重跑调度器（通常是宿主应用本身）。
    """

    def __init__(
        self,
        snapshot: hook_models.HookSnapshot,
        policy: hook_models.HookPolicy,
        *,
        project_dir: pathlib.Path,
        rabbit_data_dir: pathlib.Path,
        apps_dir: pathlib.Path,
        state_store: hook_store_ports.HookStateStore,
        run_store: hook_store_ports.HookStateStore,
        event_store: persistence_ports.SessionEventStore,
        observer: hook_event_sink.HookEventObserver|None = None,
        mcp_client: hook_runtime_ports.HookMCPClient|None = None,
        model_client: hook_runtime_ports.HookModelClient|None = None,
        handler_executor: hook_handler_execution.HookHandlerExecutor|None = None,
        plugin_roots: dict[str,str]|None = None,
        plugin_data_roots: dict[str,str]|None = None,
        configuration_paths: tuple[hook_resource_models.HookConfigurationPath,...] = (),
        configuration_fingerprint: str | None = None,
        initial_diagnostics: tuple[hook_models.HookDiagnostic,...] = (),
        configuration_refresher: hook_resource_ports.HookConfigurationRefresher|None = None,
        component_context_updater:  hook_resource_ports.HookComponentContextUpdater|None = None,
        file_watcher: hook_resource_ports.HookFileWatcher|None = None,
        async_rewake_scheduler: hook_runtime_ports.HookAsyncRewakeScheduler|None = None,
    ) -> None:
        self.snapshots = hook_compiler.HookSnapshotManager(
            snapshot,
            policy,
            initial_diagnostics,
        )
        self._state = hook_state_service.HookStateService(state_store)
        self._run_store = run_store
        self._events = hook_event_sink.AppHookRunEventSink(event_store,observer)
        self._transcript= hook_transcript.HookTranscriptProjector(event_store,apps_dir)
        self._project_dir = pathlib.Path(project_dir).resolve()
        self._rabbit_data_dir=pathlib.Path(rabbit_data_dir).resolve()
        self._app_dir= pathlib.Path(apps_dir).resolve()
        self._plugin_roots= dict(plugin_roots or {})
        self._plugin_data_roots= dict(plugin_data_roots or {})
        self._session_lifecycle_ids: dict[str,str] = {}
        self._session_lifecycle_lock= asyncio.Lock()
        self._configuration_paths = configuration_paths
        self._configuration_fingerprint= configuration_fingerprint
        self._configuration_refresher= configuration_refresher
        self._configuration_context_updater= component_context_updater
        self._file_watcher = file_watcher
        self._configuration_refresher_lock= asyncio.Lock()
        self._watch_tasks:dict[str, asyncio.Task|None]= {}
        self._watch_refresh_events:dict[str, asyncio.Event]= {}
        self._watch_generations:dict[str, str]= {}
        self._file_change_signatures:dict[str, dict[str,float]]= {}
        self._file_change_windows: dict[str,collections.deque[float]]= {}
        self._file_change_cooldowns:dict[str,float]= {}
        recorder = hook_run_recorder.HookRunRecorder(
            run_store,
            state_store=state_store,
            event_sink=self._events,
        )
        self._recorder = recorder

        executor = handler_executor or hook_handler_execution.HookHandlerExecutor(
            policy,
            mcp_client=mcp_client,
            model_client= model_client,
            artifact_store = file_hook_stores.FileHookArtifactStore(self._app_dir),
        )
        # 异步投递槽位单独留句柄：``_ack_async_deliveries`` 要对它回执，
        # ``dispose_hook_resources`` 要关它，不能只在构造时传进去就丢掉。
        self._async_sink = async_delivery.DurableHookAsyncCompletionSink(
            self.state,
            async_rewake_scheduler,
        )
        self._runtime = hook_runtime.HookRuntime(
            self.snapshots,
            policy,
            executor=executor,
            once_store = self.state,
            async_sink=self._async_sink,
        )

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------

    @property
    def runtime(self):
        """底层 hook 运行时（``HookRuntime``）。

        ``rabbit_application_composition.py`` 装配 ``HookManagementUseCases`` 时读
        的是 ``hook_lifecycle.runtime`` / ``hook_lifecycle.state`` 这两个公开名，
        而内部字段带下划线，所以这里补一对只读属性；``__init__`` 里的
        ``once_store=self.state`` 也靠它。
        """
        return self._runtime

    @property
    def state(self):
        """hook 状态服务（``HookStateService``）。"""
        return self._state

    # ------------------------------------------------------------------
    # 事件分发：PostToolBatch / MessageDisplay / SubagentStart / SubagentStop
    # ------------------------------------------------------------------

    async def post_tool_batch(
        self,
        metadata: HookLifecycleMetadata ,
        tool_calls: tuple[hook_inputs.ToolBatchCall,...],
    )-> hook_runtime_models.HookDispatchResult:
        return await self._dispatch(
            hook_inputs.PostToolBatchHookInput(
                **await self._common(metadata),
                tool_calls=tool_calls,
            ),
        )

    async def message_display(
        self,
        metadata: HookLifecycleMetadata | None = None,
        *,
        turn_id: str | None = None,
        message_id: str | None = None,
        index: int = 0,
        final: bool = False,
        delta: str = "",
    )-> hook_runtime_models.HookDispatchResult:
        return await self._dispatch(
            hook_inputs.MessageDisplayHookInput(
                **await self._common(metadata),
                turn_id=turn_id,
                message_id=message_id,
                index=index,
                final=final,
                delta=delta,
            ),
            metadata,
        )

    async def subagent_start(
        self,
        metadata: HookLifecycleMetadata ,
        *,
        agent_id: str ,
        agent_type: str,
        **extra: object,
    )-> hook_runtime_models.HookDispatchResult:
        return await self._dispatch(
            hook_inputs.SubagentStartHookInput(
                **await self._common(metadata, agent_id=agent_id, agent_type=agent_type)),
            metadata,
        )

    async def subagent_stop(
        self,
        metadata: HookLifecycleMetadata,
        *,
        agent_id: str,
        agent_type: str ,
        agent_transcript_path: str,
        last_assistant_message: str,
        stop_hook_active: bool,
        background_tasks: tuple[hook_inputs.BackgroundTasks, ...] = (),
        session_crons:tuple[hook_inputs.SessionCrons, ...] = (),
    )-> hook_runtime_models.HookDispatchResult:
        return await self._dispatch(
            hook_inputs.SubagentStopHookInput(
                **await self._common(metadata, agent_id=agent_id, agent_type=agent_type),
                stop_hook_active=stop_hook_active,
                agent_transcript_path=agent_transcript_path,
                last_assistant_message=last_assistant_message,
                background_tasks=background_tasks,
                session_crons=session_crons,
            ),
            metadata,
        )

    def has_enabled_handlers(self, event:hook_models.HookEvent) -> bool:
        """指定事件当前是否配置了钩子。

        ``observability/.../runtime_observer_bridge.py`` 用它判断要不要为
        ``MessageDisplay`` 维护攒批状态：没有钩子就完全不攒，省掉热路径开销。
        查询失败按「没有」处理（保守降级）。
        """
        snapshot=self.snapshots.current
        return snapshot.runtime_enabled and any(
            compiled.event is event and compiled.enabled for compiled in snapshot.hooks
        )

    # ------------------------------------------------------------------
    # stop 续跑的「抢占」
    # ------------------------------------------------------------------

    async def claim_stop_continuation(
        self,
        session_id: str,
        event:hook_models.HookEvent,
        decision: hook_outputs.HookDecision,
        *,
        agent_id: str | None = None,
    )->hook_store_models.HookContinuationClaim:
       requires_continuation=(
           not decision.continue_processing
           or decision.action in {hook_outputs.HookDecisionAction.BLOCK, hook_outputs.HookDecisionAction.DENY}
           or bool(decision.additional_context)
       )
       if not requires_continuation:
           await self.state.reset_stop_continuation(
               session_id,
               session_id,
               event,
               agent_id=agent_id,
           )
           return hook_store_models.HookContinuationClaim(count=0, allowed=False)
       return await self.state.increment_stop_continuation(
           session_id,
           session_id,
           event,
           agent_id=agent_id,
       )

    # ------------------------------------------------------------------
    # 生命周期事件：SessionStart / UserPromptSubmit / PreToolUse /
    #               PostToolUse / Stop / SessionEnd
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        hook_input: hook_inputs.HookInput,
        metadata: HookLifecycleMetadata ,
        *,
        environment_file: bool=False,
    ) -> hook_runtime_models.HookDispatchResult:
        session_state = await self.state.load_session(metadata.session_id, metadata.session_id)
        overlay ={} if session_state is None else dict(session_state.environment_overlay)
        env_path = self._prepare_environment_file(metadata, hook_input) if environment_file else None
        context = hook_runtime.HookDispatchContext(
            app_id= metadata.session_id,
            correlation_id = metadata.correlation_id,
            directories = hook_runtime_models.HookRuntimeDirectories(
                project_dir = str(self._project_dir),
                rabbit_data_dir = str(self._rabbit_data_dir),
                plugin_roots= self._plugin_roots,
                plugin_data_roots = self._plugin_data_roots,
            ),
            environment = metadata.environment,
            environment_overlay = overlay,
            env_file_path = str(env_path) if env_path is not None else None,
            current_model = metadata.current_model,
            current_model_profile_id=metadata.current_model_profile_id,
            compatible_model_profiles = metadata.compatible_model_profiles,
            trusted_project =metadata.trusted_project,
            recursion_suppressed= hook_recursion.is_hook_recursion_suppressed(),
            test= metadata.test,
        )

        result = await self.runtime.dispatch(hook_input, context)
        if not metadata.test:
            result = await self._apply_durable_effects(
                result,
                hook_input,
                metadata,
                overlay,
            )
        return result

    async def _dispatch(
        self,
        hook_input: hook_inputs.HookInput,
        metadata: HookLifecycleMetadata,
        *,
        environment_file: bool = False,
    ) -> hook_runtime_models.HookDispatchResult:
        """派发入口：真身失败时退化成「放行」结果，绝不向上抛。

        10 个事件/生命周期方法（session_start / user_prompt_submit /
        pre_tool_use / post_tool_use / stop / session_end / post_tool_batch /
        message_display / subagent_start / subagent_stop）都走这里；真身是上面
        的 :meth:`dispatch`（配置、观测、持久化副作用都在里面）。hook 基础设施
        未就绪时（``hook_runtime`` 等尚是 0 字节骨架）返回形状完整的
        :class:`_DegradedDispatchResult`——语义放行，主流程不受影响。

        取消必须透传：``CancelledError`` 不是「hook 失败」，吞掉它会让上层
        取消失灵。
        """
        try:
            return await self.dispatch(hook_input, metadata, environment_file=environment_file)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - hook 失败不阻断主流程
            logger.debug("hook dispatch degraded (%s): %s", type(hook_input).__name__, exc)
            return _DegradedDispatchResult(
                event=getattr(hook_input, "event", None),
                action=hook_outputs.HookDecisionAction.CONTINUE,
                continue_processing=True,
                additional_context="",
                reason="",
            )

    @typing.overload
    async def _common(
        self,
        metadata: HookLifecycleMetadata ,
        *,
        agent_id: str | None = None,
        agent_type: str | None = None,
    ) -> hook_lifecycle_contracts.HookCommonInput:...

    @typing.overload
    async def _common(
        self,
        metadata: HookLifecycleMetadata ,
        *,
        agent_id: str | None = None,
        agent_type: str | None = None,
    ) -> hook_lifecycle_contracts.HookAgentCommonInput:...

    async def _common(
        self,
        metadata: HookLifecycleMetadata ,
        *,
        agent_id: str | None = None,
        agent_type: str | None = None,
    ) -> hook_lifecycle_contracts.HookAgentCommonInput|hook_lifecycle_contracts.HookCommonInput:
        common={
            "session_id": metadata.session_id,
            "transcript_path":await self.transcript_path(metadata.session_id),
            "cwd":metadata.cwd or str(self._project_dir),
            "permission_mode": metadata.permission_mode,
            "prompt_id": metadata.prompt_id,
            "effort":metadata.effort,
        }

        if agent_id is not None and agent_type is not None:
            return hook_lifecycle_contracts.HookAgentCommonInput(
                **common,
                agent_id=agent_id,
                agent_type=agent_type,
            )
        return hook_lifecycle_contracts.HookAgentCommonInput(
            **common,
            agent_id=metadata.agent_id,
            agent_type=metadata.agent_type,
        )

    async def initialize_session(
        self,
        session_id: str ,
    ) -> str:
        async with self._session_lifecycle_lock:
            self._session_lifecycle_ids.pop(session_id,None)
        await self.stop_watching(session_id)
        async with self._session_lifecycle_lock:
            existing = self._session_lifecycle_ids.get(session_id)
            if existing is not None:
                lifecycle_id = existing
            else:
                lifecycle_id = uuid.uuid4().hex
                await self.state.begin_session_lifecycle(session_id,session_id, lifecycle_id)
                self._session_lifecycle_ids[session_id] = lifecycle_id
            self._watch_generations[session_id] = uuid.uuid4().hex
            self._watch_refresh_events[session_id] = asyncio.Event()
        await self._run_store.recover_abandoned(session_id)
        await self._run_store.enforce_retention(session_id, retention_days=14)
        await self._events.publish_status(
            session_id,
            self.snapshots.current,
            self.snapshots.diagnostics,
        )
        return lifecycle_id

    async def session_start(
        self,
        metadata: HookLifecycleMetadata ,
        source: hook_inputs.SessionStartSource,
        *,
        model: str | None = None,
        session_title: str | None = None,
    ) -> hook_runtime_models.HookDispatchResult:
        """派发 ``SessionStart``。"""
        return await self._dispatch(
            hook_inputs.SessionStartHookInput(
                **await self._common( metadata),
                source=source,
                model=model,
                session_title= session_title,
            ),
            metadata,
            environment_file=True,
        )

    async def user_prompt_submit(
        self,
        metadata: HookLifecycleMetadata ,
        prompt: str,
    ) -> hook_runtime_models.HookDispatchResult:
        return await self._dispatch(
            hook_inputs.UserPromptSubmitHookInput(
                **await self._common(metadata),
                prompt=prompt,
            ),
            metadata,
        )

    async def pre_tool_use(
        self,
        metadata: HookLifecycleMetadata,
        *,
        tool_name: str = "",
        tool_input: hook_models.JsonObject ,
        tool_use_id: str ,
    ) -> hook_runtime_models.HookDispatchResult:
        return await self._dispatch(
            hook_inputs.PreToolUseHookInput(
                **await self._common(
                    metadata,
                ),
                tool_name=tool_name,
                tool_input=tool_input,
                tool_use_id=tool_use_id,
            ),
            metadata,
        )

    async def post_tool_use(
        self,
        metadata: HookLifecycleMetadata ,
        *,
        tool_name: str,
        tool_input: hook_models.JsonObject,
        tool_response: pydantic.JsonValue,
        tool_use_id: str ,
        duration_ms: int | None = None,
    ) -> hook_runtime_models.HookDispatchResult:
        """派发 ``PostToolUse``（成功路径）。"""
        return await self._dispatch(
            hook_inputs.PostToolUseHookInput(
                **await self._common(
                    metadata,
                ),
                tool_name=tool_name,
                tool_input=tool_input,
                tool_response=tool_response,
                tool_use_id=tool_use_id,
                duration_ms=duration_ms,
            ),
            metadata,
        )

    async def stop(
        self,
        metadata: HookLifecycleMetadata ,
        *,
        last_assistant_message: str ,
        stop_hook_active: bool ,
        background_tasks: tuple[hook_inputs.BackgroundTask,...] =(),
        session_crons: tuple[hook_inputs.SessionCron,...] =(),
    ) -> hook_runtime_models.HookDispatchResult:
        """派发 ``Stop``。

        返回后调用方通常还要走 :meth:`claim_stop_continuation` 决定是否让
        这一轮「续跑」——``Stop`` 只负责产出决策，续跑名额由续跑计数器管。
        """
        return await self._dispatch(
            hook_inputs.StopHookInput(
                **await self._common(
                    metadata,
                ),
                stop_hook_active=stop_hook_active,
                last_assistant_message=last_assistant_message,
                background_tasks=background_tasks,
                session_crons=session_crons,
            ),
            metadata,
        )

    def _prepare_environment_file(
            self,
            metadata: HookLifecycleMetadata,
            hook_input: hook_inputs.HookInput,
    ) -> pathlib.Path:
        digest = hashlib.sha256(metadata.correlation_id.encode("utf-8").hexdigest())[:16]
        safe_id = session_id_safety.ensure_safe_session_id(metadata.session_id)
        path = (
                self._apps_dir / f"app_{safe_id}" / "hooks" / "environment" / f"{hook_input.hook_event_name}-{digest}.env"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink()
        return path

    async def session_end(
        self,
        metadata: HookLifecycleMetadata ,
        *,
        reason: hook_inputs.SessionEndReason,
    ) -> hook_runtime_models.HookDispatchResult:
        async with self._session_lifecycle_lock:
            lifecycle_id = self._session_lifecycle_ids.pop(metadata.session_id, None)
        await self.stop_watching(metadata.session_id)
        claimed = lifecycle_id is not None and await self.state.claim_session_end(
            metadata.session_id,
            metadata.session_id,
            lifecycle_id,
            reason,
        )
        if not claimed:
            return hook_runtime_models.HookDispatchResult(
                snapshot_revision= self.snapshots.current.revision,
                decision= hook_outputs.HookDecision(event=hook_models.HookEvent.SESSION_END),
                diagnostics=(
                    hook_models.HookDiagnostic(
                        code="session_end_lifecycle_not_active",
                        message=(
                            "SessionEnd was already claimed  or belongs to a stale App lifecycle;"
                            "handlers were not dispatched"
                        ),
                        severity= hook_models.HookDiagnosticSeverity.INFO,
                    )
                )
            )
        return await self.dispatch(
            hook_inputs.SessionEndHookInput( **await self._common(metadata), reason=reason), metadata,
        )

    # ------------------------------------------------------------------
    # 会话收尾与资源释放
    # ------------------------------------------------------------------

    async def drain_background(self, timeout_seconds: float=60.0 ) -> None:
       await self.runtime.drain_background(timeout_seconds)

    async def cancel_background(self) -> None:
        await self.runtime.cancel_background()

    # ------------------------------------------------------------------
    # 路径与环境
    # ------------------------------------------------------------------

    async def transcript_path(self, session_id: str) -> str:
        return str(await self._transcript.project(session_id))

    async def environment_overlay(
        self,
        session_id: str,
    ) -> dict[str, str]:
        session= await self.state.load_session(session_id,session_id)
        return {} if session is None else dict(session.environment_overlay)


    async def stop_watching(self, session_id: str) -> None:
       self._watch_generations.pop(session_id, None)
       self._watch_metadata.pop(session_id, None)
       hook_file_watch_coordination.clear_file_change_guard(self, session_id)
       refresh_event= self._watch_refresh_events.pop(session_id, None)
       if refresh_event is not None:
           refresh_event.set()
       task= self._watch_task.pop(session_id, None)
       if task is None:
           return
       if task is asyncio.current_task():
           return
       try:
           await asyncio.wait_for(asyncio.shield(task), timeout=_WATCHER_GRACEFUL_STOP_SECONDS)
           return
       except TimeoutError:
           task.cancel()
       except asyncio.CancelledError:
           if task.cancelled():
               return
           task.cancel()
           await asyncio.gather(task, return_exceptions=True)
           raise
       await asyncio.gather(task, return_exceptions=True)

    async def _apply_durable_effects(
        self,
        result:hook_runtime_models.HookDispatchResult,
        hook_input: hook_inputs.HookInput,
        metadata: HookLifecycleMetadata,
        existing_overlay: dict[str, str],
   )-> hook_runtime_models.HookDispatchResult:
       diagnostics= list(result.diagnostics)
       environment_updates:dict[str, str]= {}
       base_environment={**os.environ,**metadata.environment, **existing_overlay}
       for execution in result.executions:
           content= execution.environment_file_content
           if execution.environment_file_error is not None:
               diagnostics.append(
                   hook_models.HookDiagnostic(
                       severity= hook_models.HookDiagnosticSeverity.WARNING,
                       code="invalid_hook_environment_file",
                       message=execution.environment_file_error,
                       event=hook_models.HookEvent(hook_input.hook_event_name),
                       hook_id= execution.hook.hook_id,
                   )
               )
           if content is None:
               continue
           try:
                updates= hook_env_file.parse_hook_environment_file(
                    content,
                    {**base_environment, **environment_updates},
                )
                environment_updates.update(updates)
           except hook_env_file.HookEnvironmentFileError as exc:
                diagnostics.append(
                    hook_models.HookDiagnostic(
                        severity= hook_models.HookDiagnosticSeverity.WARNING,
                        code="invalid_hook_environment_file",
                        message=str(exc),
                        evnet=hook_models.HookEvent(hook_input.hook_event_name),
                        hook_id= execution.hook.hook_id,
                    )
                )
           if environment_updates:
               await self.state.set_environment_overlay(
                   metadata.session_id,
                   metadata.session_id,
                   environment_updates,
               )
           if result.decision.watch_paths is not None:
                await self.state.set_watch_paths(
                    metadata.session_id,
                    metadata.session_id,
                    result.decision.watch_paths,
                )
                refresh_event= self._watch_refresh_events.get(metadata.session_id)
                if refresh_event is not None:
                    refresh_event.set()
           if isinstance(hook_input,hook_inputs.SessionStartHookInput):
               await self.state.set_pending_session_start(
                   metadata.session_id,
                   metadata.session_id,
                   result.decision.additional_context,
                   result.decision.initial_user_message,
               )
           return result.model_copy(update={"diagnostics": tuple(diagnostics)})


































