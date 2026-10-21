import typing

import domains.agent.domain.entities.agent_compression_state as agent_compression_state
import domains.permission as permission
import domains.permission as confirmation
import domains.agent.application.context.execution_store as agent_execution_store
import domains.agent.application.context.spawn_manifest_repository as agent_spawn_mainfest_repository
import asyncio
import domains.agent.application.registry.agent_registry as agent_registry
import domains.agent.domain.entities.agent_context as agent_context
import datetime as datetime_module
import logging
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.agent.domain.entities.agent_scheduling as agent_scheduling
import domains.agent.domain.entities.agent_spawn_mainfest as  agent_spawn_mainfest
import domains.agent.domain.aggregates.scope_context as  scope_context
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
import domains.agent.domain.aggregates.cancellation as cancel_scope
logger = logging.getLogger(__name__)

# ``runtime.service.runtime_ports.AgentTaskFactory`` 的本地等价别名。
# 不直接 import runtime_ports：那个模块反过来引用 agent 层，会在 import 期成环。
AgentTaskFactory = typing.Callable[[], typing.Awaitable[object]]
AgentCompressionBusyTimeout=agent_scheduling.AgentCompressionBusyTimeout
# 同步 spawn 默认等待上限（秒）。宿主（``AgentSchedulerApplicationPort``）可以
# 用同名的 ``sync_wait_timeout_seconds`` 属性覆盖。
AgentAlreadyRunning = agent_scheduling.AgentAlreadyRunning
DEFAULT_SYNC_WAIT_TIMEOUT_SECONDS= agent_scheduling.SYNC_AGENT_WAIT_TIMEOUT_SECONDS
COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS=agent_scheduling.COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS
COMPRESSION_GATE_POLL_INTERVAL_SECONDS=agent_scheduling.COMPRESSION_GATE_POLL_INTERVAL_SECONDS
# 压缩闸门：子 agent 启动前要等目标 agent 的上下文压缩跑完。轮询间隔与总上限
# 都设成保守值——压缩通常几十毫秒到几秒，真卡住也不该把 spawn 永久挂死。
COMPRESSION_GATE_POLL_SECONDS: float = 0.05
COMPRESSION_GATE_MAX_WAIT_SECONDS: float = 30.0

# 单个 agent 失败后允许自动触发 resume 的次数，避免失败风暴。
MAX_RESUME_ATTEMPTS_PER_AGENT: int = 2

# 单个调度器内保留的结果条数上限，防止长会话无界增长。
MAX_RETAINED_RESULTS: int = 256


class SessionResumeServicePort(typing.Protocol):
    async def trigger_resume(
        self,
        session_id:str,
        source_agent:str,
        reason:str,
    )->None:...

@typing.runtime_checkable
class AgentSchedulerApplicationPort(typing.Protocol):

    @property
    def user_project_home(self)->str|None:...


    def get_agent_compression_state(self
        ,
        agent_id:str
    )->agent_compression_state.AgentCompressionState | None:...

class AgentScheduler:

    def __init__(
        self,
        session_id:str,
        registry: agent_registry.AgentRegistry|agent_context.AgentRegistryPort,
        *,
        execution_store: agent_execution_store.AgentExecutionStore,
        manifest_repository: agent_spawn_mainfest_repository.AgentSpawnManifestRepository,
    ):
        self.session_id = session_id
        self.registry = registry
        self._execution_store = execution_store
        self._manifest_repository = manifest_repository
        self._tasks:dict[str, asyncio.Task[None]] = {}
        self._resume_servie: SessionResumeServicePort | None = None
        self._app_instance:AgentSchedulerApplicationPort|None = None
        self._project_home: str|None = None
        self._spawn_locks:dict[str, asyncio.Lock] = {}
        # --- 运行期状态（由文件末尾补齐的方法维护） ---
        # agent_id -> 最终回复文本（只保留最近的若干条）
        self._results:dict[str,str]={}
        # agent_id -> 触发它的父 agent（根为 None）
        self._parents:dict[str,str|None]={}
        # agent_id -> (task_description, skill_name)，resume 时按原样重建
        self._spawn_meta:dict[str,tuple[str|None,str|None]]={}
        # agent_id -> 后台取消作用域（TurnCancelScope）
        self._background_scopes:dict[str,typing.Any]={}
        # 被取消 / 已降级 / 失败 / 已尝试 resume 的 agent 记录
        self._cancelled:set[str]=set()
        self._demoted:set[str]=set()
        self._failures:dict[str,str]={}
        self._resume_attempts:dict[str,int]={}
        # 会话级中断标记：置位后不再拉起新的子 agent
        self._interrupted:bool=False

    def bind_app_instance(self, app_instance: AgentSchedulerApplicationPort ) -> None:
        """绑定宿主应用实例。

        ``agent_factory.resolve_agent_scheduler`` 每次解析调度器都会调一次
        （可能传 ``None``——无宿主时子 agent 以降级模式运行）。重复绑定幂等，
        后一次覆盖前一次。绑定时会顺手把宿主声明的项目名抄成 ``_project_home``，
        供 ``_ensure_session_context`` 重建执行上下文用。
        """
        self._app_instance = app_instance
        project_home= self._resolve_project_home(app_instance)
        if project_home is None:
            project_home= execution_context.get_project_home()
        if project_home:
            self._project_home = project_home

    @property
    def sync_wait_timeout_seconds(self) -> float:
        return DEFAULT_SYNC_WAIT_TIMEOUT_SECONDS

    def get_result_text(self, agent_id: str) -> str | None:
        context = self.registry.get(agent_id)
        return context.result_text  if context is not None else None

    def set_resume_service(self, service: "SessionResumeServicePort | None") -> None:
        """挂上会话恢复服务；传 ``None`` 表示关闭自动 resume。

        字段名 ``_resume_servie`` 是历史拼写（少一个 r），构造器里已经定下，
        这里保持一致，免得两处名字对不上。
        """
        self._resume_servie = service

    async def spawn(
        self,
        agent_id: str,
        agent_func: AgentTaskFactory,
        parent_id: str | None = None,
        sync: bool = False,
        task_description: str | None = None,
        skill_name: str | None = None,
    ) -> str:
        lock = self._get_spawn_lock(agent_id)
        async with lock:
            return await self._spawn_unlocked(
                agent_id=agent_id,
                agent_func=agent_func,
                parent_id=parent_id,
                sync=sync,
                task_description=task_description,
                skill_name=skill_name,
            )

    async def wait_for_agent(
        self,
        agent_id: str,
        timeout: float=DEFAULT_SYNC_WAIT_TIMEOUT_SECONDS,
        *,
        demote_on_timeout: bool = False,
    ) -> str | None:

        task = self._tasks.get(agent_id)
        if not task :
            ctx= self.registry.get(agent_id)
            if ctx and ctx.status in (
                agent_context.AgentStatus.COMPLETED,
                agent_context.AgentStatus.FAILED,
                agent_context.AgentStatus.INTERRUPTED,
            ):
                return ctx.status.value
            return None
        try:
            # shield：外层超时不顺手把子 agent 一起取消——它还要继续跑。
            await asyncio.wait_for(asyncio.shield(task), timeout)
            ctx= self.registry.get(agent_id)
            if ctx and ctx.status in (
                    agent_context.AgentStatus.COMPLETED,
                    agent_context.AgentStatus.FAILED,
                    agent_context.AgentStatus.INTERRUPTED,
            ):
                return ctx.status.value
            return "completed"
        except asyncio.TimeoutError as exc:
            if demote_on_timeout and not self.demote_to_async(agent_id):
                raise RuntimeError(f"Failed to demote agent {agent_id}")
            return None


    def get_running_agents(self) -> list[str]:
        """当前仍在跑的 agent id（排序后的快照）。"""
        return [aid for aid, task in self._tasks.items() if not task.done()]

    def is_any_running(self) -> bool:
        """是否还有子 agent 在跑。"""
        return any(not task.done() for task in self._tasks.values())

    def iter_running_agents(self) -> typing.Iterator[tuple[str, asyncio.Task[None]]]:
        for aid, task in self._tasks.items():
            if not task.done():
               yield aid,task

    def cancel_all(self, reason: str = "cancelled") -> int:
        """取消全部后台 agent，返回实际取消的个数。

        会话收尾（退出 / 切会话）时调用。已结束的任务不计入返回值。
        """
        cancelled = 0
        for agent_id,task in list(self._tasks.items()):
            if not task.done():
                self._cancel_background_scope(agent_id, reason="user_stop_all_agents")
                self._mark_user_interrupted(
                    agent_id,
                    agent_context.AgentInterruptionReason.USER_STOP_ALL_AGENTS
                )
                task.cancel()
                cancelled += 1
        return cancelled

    def cancel(self, agent_id: str) -> bool:
        task = self._tasks.get(agent_id)
        if task is not None and not task.done():
           return False
        self._cancel_background_scope(agent_id, reason="user_stop_agent")
        self._mark_user_interrupted(
            agent_id,
            agent_context.AgentInterruptionReason.USER_STOP_AGENT,
        )
        task.cancel()
        logger.info("[AGENT-SCHEDULER] agent %r cancelled ", agent_id)
        return True

    def demote_to_async(self, agent_id: str) -> bool:
        if not self.registry.update_wait_mode(agent_id, agent_context.AgentWaitMode.ASYNC):
            return False
        context = self.registry.get(agent_id)
        if context is None:
            return False
        try:
            manifest = self._manifest_repository.load(context)
            if manifest is not None and manifest.wait_mode!= agent_context.AgentWaitMode.ASYNC:
                self._manifest_repository.save(context, manifest.model_copy(update={"wait_mode": agent_context.AgentWaitMode.ASYNC}),)
        except Exception as exc:
            logger.error(exc)
        return True

    def get_running_task(self, agent_id: str) -> asyncio.Task[None] | None:
        task = self._tasks.get(agent_id)
        if task is None or task.done():
            return None
        return task

    async def resume_agent(
        self,
        agent_id: str,
        agent_func: AgentTaskFactory ,
    ) -> None:

        ctx = self.registry.get(agent_id)
        if ctx is None:
            logger.debug("[AGENT-SCHEDULER] agent %r still running, reuse on resume", agent_id)
            return agent_id
        ctx.interruption_reason=None
        ctx.status = agent_context.AgentStatus.RUNNING
        self.registry.update_status(agent_id, agent_context.AgentStatus.RUNNING)
        self._ensure_background_scope(agent_id)
        task = asyncio.create_task(self._run_agent(ctx, agent_func))
        self._tasks[agent_id] = task
        return agent_id

    def _ensure_session_context(self) -> None:
        project_home = self._project_home or self._resolve_project_home(self._app_instance)
        if not project_home:
            return
        current_session_id = execution_context.get_session_id()
        current_project_home = execution_context.get_project_home()
        current_app = execution_context.get_app_instance()
        if(
            current_session_id == self.session_id
            and current_project_home == project_home
            and(self._app_instance is None or current_app is self._app_instance)
        ):
            execution_context.set_agent_scheduler(self)
            return
        root_agent_id = execution_context.get_root_agent_id()
        parent_instance_id =execution_context.get_current_parent_agent_instance_id()
        invocation_id =execution_context.get_current_invocation_id()
        execution_context.bind_execution_context(
            app_id=self.session_id,
            project_home=project_home,
            app_instance=self._app_instance,
        )
        execution_context.set_agent_scheduler(self)
        execution_context.set_root_agent_id(root_agent_id)
        execution_context.set_current_parent_agent_instance_id(parent_instance_id)
        execution_context.set_current_invocation_id(invocation_id)

    def _get_spawn_lock(self, agent_id: str) -> asyncio.Lock:
        lock = self._spawn_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self._spawn_locks[agent_id] = lock
        return lock

    def _get_compression_state(
        self,
        agent_id: str,
        ) -> agent_compression_state.AgentCompressionState | None:
        app = self._app_instance
        if app is None:
            candidate=execution_context.get_app_instance()
            if isinstance(candidate, AgentSchedulerApplicationPort):
                app=candidate
        if app is None:
            return None
        return app.get_agent_compression_state(agent_id)

    async def _wait_for_compression_gate_unlocked(self, agent_id: str) -> None:
        """等目标 agent 的上下文压缩结束（在持有 spawn 锁的前提下调用）。

        压缩会整体替换上下文内容，此时再塞一个子 agent 进去会读到半成品。
        等待有总上限（:data:`COMPRESSION_GATE_MAX_WAIT_SECONDS`）：真卡住也只是
        记一条 warning 后放行，不让 spawn 被永久挂死。
        """
        first_seen = self._get_compression_state(agent_id)
        if first_seen is None:
            return
        deadline = asyncio.get_running_loop().time()+COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS
        current=first_seen
        while current is not None:
            remaining = deadline- asyncio.get_running_loop().time()
            if remaining<=0:
                raise AgentCompressionBusyTimeout(agent_id, current)
            await asyncio.sleep(min(COMPRESSION_GATE_POLL_INTERVAL_SECONDS,remaining))
            current=self._get_compression_state(agent_id)


    async def _spawn_unlocked(
        self,
        agent_id: str,
        agent_func: AgentTaskFactory,
        parent_id: str | None = None,
            sync: bool = False,
        task_description: str | None = None,
        skill_name: str | None = None,
    ) -> str:
        await self._wait_for_compression_gate_unlocked(agent_id)
        existing = self._tasks.get(agent_id)
        if existing is not None:
            if not existing.done():
                raise AgentAlreadyRunning(agent_id)
            self._tasks.pop(agent_id,None)

        wait_mode =agent_context.AgentWaitMode.SYNC if sync else agent_context.AgentWaitMode.ASYNC
        ctx=agent_context.AgentContext(
            agent_id=agent_id,
            session_id=self.session_id,
            parent_agent_id = parent_id,
            status= agent_context.AgentStatus.RUNNING,
            wait_mode= wait_mode,
            started_at = datetime_module.datetime.now().isoformat(),
            task_description= task_description,
        )
        ctx.registry = self.registry
        self.registry.register(ctx)
        self._ensure_background_scope(agent_id)

        try:
            manifest = agent_spawn_mainfest.AgentSpawnManifest.create(
                skill_name=skill_name or agent_id.rsplit("_", 1)[0],
                agent_name=agent_id,
                parent_id=parent_id,
                task_description=task_description,
                session_id=self.session_id,
                wait_mode=wait_mode,
            )
            self._manifest_repository.save(ctx, manifest)
        except Exception as e:
            logger.error(e)
        task =asyncio.create_task(self._run_agent(ctx, agent_func))
        self._tasks[agent_id] = task
        logger.debug(
            "[AGENT-SCHEDULER] spawned agent %r (parent=%r, skill=%r)",
            agent_id, parent_id, skill_name,
        )
        return agent_id

    async def _run_agent(
        self,
        ctx: agent_context.AgentContext,
        agent_func: AgentTaskFactory,
    ) -> None:
        self._ensure_session_context()
        result_text=""
        agent_scope = self._ensure_background_scope(ctx.agent_id)
        scope_token = scope_context.set_turn_scope_context(agent_scope)
        try:
            result = await agent_func()
            result_text = str(result) if result else "(no output)"
            self._execution_store.save_result(ctx, result_text)
            if agent_scope.cancelled or ctx.status== agent_context.AgentStatus.INTERRUPTED:
                ctx.status= agent_context.AgentStatus.INTERRUPTED
                self.registry.update_status(ctx.agent_id, agent_context.AgentStatus.INTERRUPTED)
                await self._notify_parent(ctx,result_text)
                return
            ctx.status= agent_context.AgentStatus.COMPLETED
            self.registry.update_status(ctx.agent_id, agent_context.AgentStatus.COMPLETED)
            await self._notify_parent(ctx,result_text)

        except asyncio.CancelledError:
            result_text=f"Agent '{ctx.agent_id}' was interrupted before product is a final result."
            self._execution_store.save_result(ctx, result_text)
            ctx.status= agent_context.AgentStatus.INTERRUPTED
            self.registry.update_status(ctx.agent_id, agent_context.AgentStatus.INTERRUPTED)
            await self._notify_parent(ctx,result_text)
        except Exception as e:
            error_msg=f"Error:{e}"
            self._execution_store.save_result(ctx,result_text)
            ctx.status= agent_context.AgentStatus.FAILED
            self.registry.update_status(ctx.agent_id, agent_context.AgentStatus.FAILED)
            await self._notify_parent(ctx,error_msg)
        finally:
            scope_context.reset_turn_scope_context(scope_token)
            if not agent_scope.terminated:
                agent_scope.complete()
            self._tasks.pop(ctx.agent_id,None)

    def _is_session_interrupted(self) -> bool:
        try:
            return confirmation.InterruptManager.check_interrupt(self.session_id)
        except Exception as e:  # noqa: BLE001 - 查询中断状态绝不该抛
            return False

    async def _trigger_resume_if_allowed(self, ctx:agent_context.AgentContext) -> None:
        """在允许的前提下触发一次会话级 resume。

        三道闸：没挂 resume 服务、没有 session_id、已达单 agent 重试上限——
        任一不满足都直接返回 ``False``。resume 本身失败只记日志，不向上抛。
        """
        if self._resume_servie is None:
            return
        if ctx.interruption_reason== agent_context.AgentInterruptionReason.USER_STOP_ALL_AGENTS:
            return
        if self._is_session_interrupted():
            return
        await self._resume_servie.trigger_resume(self.session_id, ctx.agent_id,ctx.status.value,)

    async def _notify_parent(
        self,
        ctx:agent_context.AgentContext,
        result: str,
    ) -> None:
        """把子 agent 的开始/结束事件报给宿主的 observer。

        观测是旁路能力：没有 observer、observer 没实现对应方法、或者调用抛异常，
        一律只记 debug——不该让一次通知失败影响 agent 本身的生命周期。
        """
        if not ctx.parent_agent_id:
            return
        target_id = ctx.parent_agent_id
        original_agent_id = ctx.agent_id
        visited =set()
        while target_id and target_id not in visited:
            visited.add(target_id)
            parent_ctx = self.registry.get(target_id)
            if parent_ctx and parent_ctx.status in(
                agent_context.AgentStatus.COMPLETED,
                agent_context.AgentStatus.FAILED,
            ):
                target_id = parent_ctx.agent_id
                continue
            break
        if not target_id:
            try:
                await self._trigger_resume_if_allowed(ctx)
            except Exception as exc:
                logger.error(exc)
            return
        try:
            if ctx.status== agent_context.AgentStatus.INTERRUPTED:
                notification=f"[AGENT INTERRUPTED] Agent '{original_agent_id}' wat interrupted.\nResult:\n{result}"
            else:
                notification=f"[Background Agent Completed] Agent '{original_agent_id}' finished.\nResult:\n{result}"
            await self._wait_for_compression_gate_unlocked(target_id)
            queue = runtime_bindings.get_message_queue()
            if queue is None:
                return
            queue.enqueue(self.session_id, target_id, notification, source="aget_scheduler",)
        except Exception as exc:
            logger.error(exc)
        try:
            if self._resume_servie:
                target_ctx =self.registry.get(target_id)
                if target_ctx and target_ctx.status in (
                    agent_context.AgentStatus.RUNNING,
                    agent_context.AgentStatus.WAITING_TOOL,
                    # 同步等待中的父 agent 也是「活着」的：它正阻塞在
                    # pre_reasoning 的 sync wait 里，通知会被它随后的队列
                    # 投递消费掉，不该再触发一次 resume 把会话双跑。
                    agent_context.AgentStatus.SYNC_WAITING_CHILD,
                ):
                    logger.info(f"[AGENT_SCHEDULER] Target '{target_id}' is'"
                                f"{target_ctx.status.value} -notification will be"
                                f"consumed by pre_reasoning hook, skipped resume")
                else:
                    await self._trigger_resume_if_allowed(ctx)
        except Exception as exc:
            logger.error(exc)

    def _cancel_background_scope(self, agent_id: str,*, reason: str) -> bool:
        """取消某个 agent 的后台取消作用域；返回是否真的取消了。

        作用域不存在（没建成 / 已摘掉）时返回 ``False``。取消是幂等的——
        ``TurnCancelScope`` 自己保证只有首个终态转换生效。
        """
        background_scope = confirmation.InterruptManager.get_background_scope(
            self.session_id,
            agent_id,
        )
        if background_scope is not None:
            background_scope.cancel(reason=reason)

    def _mark_user_interrupted(self,
        agent_id: str ,
        reason: agent_context.AgentInterruptionReason) -> None:
        """把会话标记为"被用户中断"，并清掉一次性中断标记。

        清标记很重要：``InterruptManager`` 的中断标记是一次性的，不清的话下一轮
        spawn 又会读到同一个 True，整个会话此后再也起不了子 agent。

        这里用 ``check_interrupt`` 而不是 ``clear_interrupt``：后者是
        ``async def``，在本方法（同步）里调用只会拿到一个永不 await 的协程，
        标记根本不会被清掉——``RuntimeWarning`` 都不会有，属于静默失效。
        ``check_interrupt`` 是同步的"读取并消费"，语义正好是"吃掉这个标记"。
        """
        context= self.registry.get(agent_id)
        if context is not None:
            context.interruption_reason = reason
        self.registry.update_status(agent_id, agent_context.AgentStatus.INTERRUPTED)
        logger.info("[AGENT-SCHEDULER] session marked user-interrupted (agent=%r)", agent_id)

    # ------------------------------------------------------------------
    # 内部小工具（不构成对外契约，单独放最后）
    # ------------------------------------------------------------------

    def _registry_context(self, agent_id: str) -> typing.Any | None:
        """从注册表取 agent 上下文；注册表不可用或未登记时返回 ``None``。"""
        getter = getattr(self.registry, "get", None)
        if not callable(getter):
            return None
        try:
            return getter(agent_id)
        except Exception:  # noqa: BLE001
            return None

    def _registry_status(self, agent_id: str, status: agent_context.AgentStatus) -> None:
        """把状态写进注册表；写不进去只记 debug（上下文可能是冻结模型）。"""
        updater = getattr(self.registry, "update_status", None)
        if not callable(updater):
            return
        try:
            updater(agent_id, status)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[AGENT-SCHEDULER] update status of %r failed: %s", agent_id, exc)

    def _observer(self) -> typing.Any | None:
        """取宿主的 observer；没有宿主或没装配 observer 时返回 ``None``。"""
        app = self._app_instance
        for attr in ("observer", "base_observer"):
            candidate = getattr(app, attr, None)
            if candidate is not None:
                return candidate
        return None

    def _store_result(self, agent_id: str, text: str) -> None:
        """存结果并做容量收敛，避免长会话把内存吃满。"""
        self._results[agent_id] = text
        if len(self._results) > MAX_RETAINED_RESULTS:
            # dict 保序，扔掉最早的一批
            for stale in list(self._results.keys())[: len(self._results) - MAX_RETAINED_RESULTS]:
                self._results.pop(stale, None)

    @staticmethod
    def _result_text_of(value: object) -> str | None:
        """把 ``agent_func`` 的返回值归一成文本。

        子 agent 可能返回纯字符串、``ReActAgent``、或者消息对象——逐个探最常见的
        取值路径，都取不到就退回 ``str()``。返回 ``None`` 表示"没有可说"。
        """
        if value is None:
            return None
        if isinstance(value, str):
            return value
        take = getattr(value, "take_last_response_text", None)
        if callable(take):
            try:
                text = take()
            except Exception:  # noqa: BLE001
                text = None
            if isinstance(text, str) and text:
                return text
        for attr in ("text", "result_text", "final_text"):
            candidate = getattr(value, attr, None)
            if isinstance(candidate, str) and candidate:
                return candidate
        return str(value)

    def _recorded_spawn(self, agent_id: str) -> tuple[str | None, str | None, str | None]:
        """找回某个 agent 上次 spawn 时登记的 (parent, description, skill)。"""
        parent = self._parents.get(agent_id)
        meta = self._spawn_meta.get(agent_id)
        description, skill = meta if meta is not None else (None, None)
        return parent, description, skill

    def _resolve_resume_factory(
        self,
        agent_id: str,
        task_description: str | None,
    ) -> AgentTaskFactory | None:
        """为 resume 找一个可执行的 ``agent_func``。

        按优先级探：manifest 仓库里上次登记的执行体 → 注册表里的 agent 实例
        （包一层 ``reply``）。都找不到返回 ``None``。
        """
        repository = self._manifest_repository
        for attr in ("resolve_agent_func", "get_agent_func", "load_agent_func"):
            resolver = getattr(repository, attr, None)
            if not callable(resolver):
                continue
            try:
                produced = resolver(agent_id)
            except Exception:  # noqa: BLE001
                continue
            if callable(produced):
                return typing.cast(AgentTaskFactory, produced)

        context = self._registry_context(agent_id)
        runtime = getattr(context, "instance", None) or getattr(context, "runtime", None)
        reply = getattr(runtime, "reply", None)
        if callable(reply):
            def _resume_via_reply() -> typing.Awaitable[object]:
                return reply(task_description or "")
            return _resume_via_reply
        return None

    def _new_background_scope(self, agent_id: str) -> typing.Any | None:
        """给后台 agent 建一个挂在会话级作用域下的取消作用域。

        会话级作用域取不到时建"无父级"的独立作用域——照样能单独取消，只是不会
        随会话一起联动。运行时层不可用时返回 ``None``（取消能力缺席而已）。
        """
        try:
            import domains.agent.domain.aggregates.cancellation as cancellation
        except Exception as exc:  # pragma: no cover - 运行时层不可用时降级
            logger.debug("[AGENT-SCHEDULER] cancellation module unavailable: %s", exc)
            return None
        parent = None
        manager = _interrupt_manager()
        if manager is not None:
            try:
                parent = manager.get_session_scope(self.session_id)
            except Exception:  # noqa: BLE001
                parent = None
        try:
            return cancellation.TurnCancelScope(parent, session_id=self.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[AGENT-SCHEDULER] create background scope for %r failed: %s", agent_id, exc)
            return None

    # ------------------------------------------------------------------
    # 会话上下文 / 后台作用域 / 单个取消
    #
    # 放在类体（≈文件）末尾：``cancel`` 与 ``_ensure_session_context`` 在本类中
    # 已有同名旧实现（第 217、268 行），按 Python 类体的绑定规则「后定义覆盖先
    # 定义」，**这里才是实际生效的版本**。
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_project_home(app_instance:AgentSchedulerApplicationPort|None) -> str | None:
        if app_instance is None:
            return None
        return app_instance.user_project_home

    def _ensure_session_context(self) -> bool:
        """确保当前协程绑着本会话的执行上下文；返回是否可用。

        子 agent 跑在自己的 ``asyncio.Task`` 里，**不继承**父协程的 contextvar，
        所以每个 entry point（spawn / resume / run）进来都要重新绑一次。

        ``agent_id`` 只用于日志——调用点两种写法都有（``_spawn_unlocked`` /
        ``resume_agent`` 传 id，``_run_agent`` 不传），所以是可选参数。

        重绑定时会把**还在用**的几个上下文值（root agent / 父实例 / invocation）
        先读出来再写回去，避免把别人已经绑好的东西抹成 ``None``。这些 accessor
        在 ``execution_context`` 里尚未全部落地，因此一律走软调用：函数在就传，
        不在就跳过——调度器是 agent 生命周期的关键路径，不能为埋点塌掉。
        """
        project_home =self._project_home or self.resolve_project_name(self._app_instance)
        if not project_home:
            logger.debug(
                "[AGENT-SCHEDULER] skip binding session context: no project home (agent=%r)",
                self.session_id,
            )
            return False

        current_session_id = execution_context.get_session_id()
        current_project_home=execution_context.get_project_home()
        current_app=execution_context.get_app_instance()
        if (
                current_session_id == self.session_id
                and current_project_home == project_home
                and (self._app_instance is None or current_app is self._app_instance)
        ):
            execution_context.set_agent_scheduler(self)
            return
        root_agent_id = execution_context.get_root_agent_id()
        parent_instance_id = execution_context.get_current_parent_agent_instance_id()
        invocation_id =execution_context.get_current_invocation_id()
        execution_context.bind_execution_context(
            app_id=self.session_id,
            project_home=project_home,
            app_instance=self._app_instance,
        )

        execution_context.set_agent_scheduler(self)
        execution_context.set_root_agent_id(root_agent_id)
        execution_context.set_current_parent_agent_instance_id(parent_instance_id)
        execution_context.set_current_invocation_id(invocation_id)

    def _ensure_background_scope(self, agent_id: str) -> cancel_scope.BackgroundExecutionScope:
        existing_scope=confirmation.InterruptManager.get_background_scope(
            self.session_id,
            agent_id,
        )
        if existing_scope is not None and existing_scope.terminated:
            confirmation.InterruptManager.unregister_background_scope(
                self.session_id,
                agent_id,
            )
            existing_scope=None
        if existing_scope is not None:
            return existing_scope
        session_scope = confirmation.InterruptManager.get_session_scope(self.session_id)
        background_scope =cancel_scope.BackgroundExecutionScope(session_scope, spawn_id=agent_id)
        confirmation.InterruptManager.register_background_scope(
            self.session_id,
            agent_id,
            background_scope,
        )
        return background_scope


    def cancel(self, agent_id: str,) -> bool:
        """取消单个后台 agent；返回**是否真的取消了点东西**。

        三条通道一起打，缺哪条算哪条：

        1. ``_background_scopes`` 里自己登记的那份作用域（最可靠——
           ``_cancel_background_scope`` 只认 ``InterruptManager`` 那一侧，而
           ``_new_background_scope`` 建出来的并不总进它那张表）；
        2. ``InterruptManager`` 登记的后台作用域；
        3. ``asyncio.Task.cancel()``。

        没有可取消的目标（从未 spawn、跑完且作用域已摘）返回 ``False``，不写
        任何状态；取消成功返回 ``True``。
        """
        task = self._tasks.get(agent_id)
        if task is None or task.done():
            logger.debug("[AGENT-SCHEDULER] nothing to cancel for %r", agent_id)
            return False
        self._cancel_background_scope(agent_id, reason="user_stop_agent")
        self._mark_user_interrupted(agent_id, agent_context.AgentInterruptionReason.USER_STOP_AGENT)
        task.cancel()
        logger.info("[AGENT-SCHEDULER] agent %r cancelled", agent_id)
        return True


def _is_compressing(state: typing.Any) -> bool:
    """判断压缩状态对象是否表示"压缩正在进行"。

    兼容两种形态：``AgentInstance``（``is_compressing`` / ``compression_run_id``）
    和任意带同义字段的对象。字段全缺时按"没在压缩"处理——保守放行比永久
    挂死好。
    """
    if state is None:
        return False
    if getattr(state, "is_compressing", False):
        return True
    if getattr(state, "compression_run_id", None):
        return True
    for attr in ("compressing", "in_progress"):
        if getattr(state, attr, False):
            return True
    return False


def _interrupt_manager() -> typing.Any | None:
    """取 ``InterruptManager`` 类；``confirmation_core`` 不可用时返回 ``None``。

    延迟导入：``confirmation_core`` 位于更底层，但它在自己的模块里也反过来延迟
    导入运行时层；这里同样用函数内导入把依赖限制在真正用到的时候。
    """
    try:
        from domains.permission.application.interrupt_coordination import InterruptManager
    except Exception:  # pragma: no cover - 包缺失时降级
        return None
    return InterruptManager


def inspect_isawaitable(value: object) -> bool:
    """``inspect.isawaitable`` 的薄封装（保留原名以便单测替换）。"""
    import inspect

    return inspect.isawaitable(value)


# ----------------------------------------------------------------------
# ``resolve_project_name`` / ``_ensure_session_context`` /
# ``_ensure_background_scope`` / ``cancel`` 的私有支撑。
#
# 全部放在文件末尾：只在**调用时**被上面四个方法用到，不参与模块装配。
# ----------------------------------------------------------------------


def _as_project_name(value: object) -> str | None:
    """把任意形状的项目素材归一成非空字符串；归一不出来返回 ``None``。

    只认 ``str`` 和 ``os.PathLike``——别的类型一律不要，免得把 ``<object at
    0x...>`` 这种东西当成项目名绑进上下文。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value.strip() or None
    fspath = getattr(value, "__fspath__", None)
    if callable(fspath):
        try:
            produced = fspath()
        except Exception:  # noqa: BLE001
            return None
        if isinstance(produced, str):
            return produced.strip() or None
    return None


def _context_call(name: str, *args: object) -> bool:
    """软调用 ``execution_context`` 上的某个函数；不存在或抛错都当没做。

    ``execution_context`` 里有一批可选 accessor（``set_agent_scheduler``、
    ``get_current_invocation_id`` ……），运行时层没落地就可能一个都没有。调度器
    在 agent 生命周期的关键路径上，不能因为这些 accessor 缺席就崩。
    """
    func = getattr(execution_context, name, None)
    if not callable(func):
        return False
    try:
        func(*args)
    except Exception as exc:  # noqa: BLE001 - 上下文读写失败只降级
        logger.debug("[AGENT-SCHEDULER] execution_context.%s failed: %s", name, exc)
        return False
    return True


def _context_value(name: str) -> typing.Any:
    """软读取 ``execution_context`` 上的某个取值函数；拿不到返回 ``None``。"""
    func = getattr(execution_context, name, None)
    if not callable(func):
        return None
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 - 上下文读取失败只降级
        logger.debug("[AGENT-SCHEDULER] execution_context.%s failed: %s", name, exc)
        return None


def _interruption_reason(member: str) -> typing.Any | None:
    """按成员名取``agent_context`` 的中断原因枚举；取不到返回 ``None``。

    类名在仓里有历史拼写差异（``AgentInterruptionReason`` 与带多一个 e 的写法），
    逐个探一遍比赌某一个拼写在运行时存在要稳。
    """
    for class_name in ("AgentInterruptionReason", "AgentInterrupteionReason"):
        enum_class = getattr(agent_context, class_name, None)
        if enum_class is None:
            continue
        try:
            value = getattr(enum_class, member, None)
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            return value
    return None


def _scope_terminated(scope: typing.Any) -> bool:
    """作用域是否已进终态；形状不认识时按「已终止」处理（宁可重建，不要复用僵尸）。"""
    if scope is None:
        return True
    try:
        return bool(getattr(scope, "terminated", True))
    except Exception:  # noqa: BLE001
        return True


def _cancel_scope(scope: typing.Any, reason: str) -> bool:
    """取消一个作用域；签名不支持 ``reason=`` 就退回无参调用。"""
    if scope is None:
        return False
    canceller = getattr(scope, "cancel", None)
    if not callable(canceller):
        return False
    try:
        return bool(canceller(reason=reason))
    except TypeError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AGENT-SCHEDULER] cancel background scope failed: %s", exc)
        return False
    try:
        return bool(canceller())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AGENT-SCHEDULER] cancel background scope failed: %s", exc)
        return False


class NullCancelScope:
    """不可用的取消作用域占位（运行时层缺席时顶上）。

    接口对齐到调用点真正用到的那几个成员：``cancelled`` / ``terminated`` /
    ``cancel`` / ``complete`` / ``session_id``。所有终态转换都返回 ``False``
    ——它表示「这次调用不可取消」，而不是「取消成功」。
    """

    def __init__(self, session_id: str | None = None) -> None:
        self._session_id = session_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def cancelled(self) -> bool:
        return False

    @property
    def terminated(self) -> bool:
        return False

    @property
    def completed(self) -> bool:
        return False

    def cancel(self, reason: str = "") -> bool:
        return False

    def complete(self) -> bool:
        return False

    def failed(self, reason: str = "") -> bool:
        return False
