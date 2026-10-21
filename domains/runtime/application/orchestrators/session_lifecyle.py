"""会话生命周期协调器（SessionLifecycleCoordinator）。

``SessionOrchestrator.__init__`` 构造它（``app`` + ``owner`` 两个引用），
``SessionOrchestrator.init_session`` 把「解析会话 id → 绑定请求上下文 →
登记会话标识 →（恢复时）查持久化并转交 hydrator」这套骨架动作委托到这里。

调用契约（``session_orchestrator.py:155``）：

- 位置参数 ``(session_id, is_resume)``；
- 关键字参数 ``persistence_factory``（``SessionPersistence``，目前只有静态查询
  方法）与 ``runtime_context_provider``（``get_runtime_context``，运行上下文
  未安装时会 raise）。

当前阶段宿主还没有会话实体（``new_session_manager`` 未装配、
``SessionStateHydrator`` 只有 ``__init__``），所以恢复只做探测：缺哪一环就
跳过哪一环，绝不向上抛——初始化失败会直接阻断整个会话建立，比不恢复严重得多。
"""

from __future__ import annotations

import datetime as datetime_module
import logging
import typing
import uuid
import pathlib

import domains.runtime.application.orchestrators.session_lifecycle_contracts as lifecycle_contracts
import domains.runtime.application.request_context as app_request_context
import domains.session.infrastructure.session_persistence as session_persistence_module
import domains.configuration.domain.runtime_state.execution_context as execution_context_module
from domains.plugin.application import plugin_contracts
import domains.plugin.application.request_context as plugin_context

logger = logging.getLogger(__name__)

__all__ = ["SessionLifecycleCoordinator"]

_LOG_PREFIX = "[SESSION-LIFECYCLE] "


def _safe_str(value: object) -> str | None:
    """把任意值规整成字符串；空 / 非字符串返回 ``None``（空模块字段容错）。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None

# SessionStateHydrator 落地后，恢复入口会叫下面某个名字；现在一个都不存在，
# 探测不到就跳过（见 _hand_off_to_hydrator）。
_HYDRATOR_ENTRY_POINTS: tuple[str, ...] = (
    "hydrate_session",
    "hydrate",
    "resume_session",
    "restore_session",
)


class SessionLifecycleCoordinator:


   def __init__(
        self,
        app: lifecycle_contracts.SessionLifecycleApplication,
        owner: lifecycle_contracts.SessionLifecycleOwner,
   ) -> None:
       self.app = app
       self.owner = owner

   async def init_session(
        self,
        session_id: str | None = None,
        is_resume: bool = False,
        *,
        persistence_factory: type[session_persistence_module.SessionPersistence] ,
        runtime_context_provider: lifecycle_contracts.RuntimeContextProvider,
   ) -> str:
        session_id,resume = self._resolve_session_request(session_id, is_resume)
        self._bind_request_context(session_id)
        self._configure_persistence(
           persistence_factory= persistence_factory,
           runtime_context_provider= runtime_context_provider,
        )
        await self._initiallize_persisted_session(session_id, resume=resume)
        if resume:
            await self.restore_agent_states_on_resume(session_id)
        else:
            self.app.execution_context= self._load_execution_context(session_id, resume=False)
            await self._preload_sub_agents()

        await self._notify_session_initialized(session_id, resume=resume)
        return session_id

   def _resolve_session_request(
        self,
        session_id: str | None,
        is_resume: bool | None,
   ) -> tuple[str, bool]:
        resolved_id = session_id or str(uuid.uuid4())
        if session_id is None:
            logger.debug("generated new session id: %s", resolved_id)
        # 返回 resolved_id 而不是形参 —— 形参可能是 None，
        # 而后续所有环节（绑定上下文、持久化、通知）都需要一个真实 id。
        return resolved_id, bool(is_resume) if is_resume is not None else False

   def _configure_persistence(
        self,
        *,
        persistence_factory: typing[session_persistence_module.SessionPersistence],
        runtime_context_provider: lifecycle_contracts.RuntimeContextProvider,
   ) -> None:
        runtime_context= runtime_context_provider()
        sessions_dir = runtime_context.sessions_dir or runtime_context.user_data_home/"sessions"
        plane= self.app.persistence_plane
        state_store = plane.session_state if plane is not None else None
        self.app.session_persistence = persistence_factory(
            sessions_dir=sessions_dir,
            session_state_store=state_store,
        )
        logger.debug(
            "session persistence configured: factory=%s sessions_dir=%s state_store=%s",
            getattr(persistence_factory, "__name__", persistence_factory),
            sessions_dir,
            type(state_store).__name__ if state_store is not None else None,
        )

   async def _initiallize_persisted_session(
        self,
        session_id: str,
        *,
        resume: bool,
   ) -> None:
        if resume:
            # resume 分支：恢复失败属致命错误，必须向上抛，绝不能被静默吞掉。
            try:
                await self._recover_interrupted_agents(session_id)
            except Exception as e:
                raise RuntimeError("Failed to initialize session") from e
            # 会话实体尚未装配（new_session_manager 未接、SessionStateHydrator
            # 只有 __init__），绑定当前会话的钩子点暂时是 no-op；待装配后再在此接入。
            return

        # 非 resume 分支：先在内存里无条件创建 ExecutionContext。
        # 这一步不能放进下面的 “持久化降级” try 里——一旦它抛错被吞掉，
        # app.execution_context 会保持 None，后续 _load_execution_context 才会
        # 冒出误导性的 “New Session initialization did not create an ExecutionContext”。
        await self._prepare_new_session_context(session_id)
        try:
            await self.owner.save_app_state_and_agents(session_id)
        except Exception:
            # 只有“持久化未接入/落盘失败”才是可接受的降级；ExecutionContext 已就绪，不影响会话建立。
            logger.debug(
                "%sno persistence factory wired; fresh session stays in-memory",
                _LOG_PREFIX,
            )
            self.app.session_persistence = None
        # 同上：会话实体未装配，绑定当前会话的钩子点暂时 no-op。

   async def restore_agent_states_on_resume(self, session_id: str) -> None:
        bootstrap_mode = self.app.app_mode
        await self.owner.load_app_state_and_agents(session_id)
        execution_context = self.app.execution_context
        if execution_context is None:
            raise RuntimeError("Hydrated Appstate did not provider an ExecutionContext")
        snapshot_count_before= len(execution_context.agent_config_snapshots)
        self.app._agent.reconcile_hydarated_configuration(
            bootstrap_mode=bootstrap_mode,
        )
        snapshot_count_after= len(execution_context.agent_config_snapshots)
        if snapshot_count_before==0 and snapshot_count_after >0:
            logger.info("agent config snapshot after appstate hydartion")
        activated_default= await self.activate_default_agent_fro_empty_resume(session_id)
        if not activated_default:
            await self.rebind_default_agent_runtime_after_resume()
        self._sanitize_agent_contexts()
        self._log_plan_reconciliation()

   def _load_execution_context(
        self,
        session_id: str,
        *,
        resume: bool,
   ) -> execution_context_module.ExecutionContext:
       if not resume:
           context= self.app.execution_context
           if context is None:
               raise RuntimeError("New Session initialization dia not create an ExecutionContext")
           return context
       context= self.app.session_manager.load_session_context(session_id)
       if context is None:
           return self.app.session_manager.create_execution_context(session_id=session_id, working_directory=str(self.app.user_project_home),)
       self._log_resume_execution_context()
       return context

   async def _prepare_new_session_context(self, session_id: str) -> None:
       """为新会话创建初始 ``ExecutionContext``。

       新会话没有可恢复的持久化状态，这里直接构造一份空运行态快照并挂到
       ``app.execution_context`` 上；随后的 ``save_app_state_and_agents`` 会基于它
       再落盘一次。``_load_execution_context`` 对非 resume 分支会读回这个属性，
       缺失时才会触发 “did not create an ExecutionContext” 断言。
       """
       now = datetime_module.datetime.now().isoformat()
       self.app.execution_context = execution_context_module.ExecutionContext(
           session_id=session_id,
           created_at=now,
           last_updated=now,
           working_directory=str(self.app.user_project_home),
           app_mode=self.app.app_mode,
           current_plan=None,
           historical_plans=[],
           group_chat_selected_expert_ids=[],
           group_chat_selected_skills=None,
           agent_config_id=None,
           role_selection=None,
           agent_config_snapshots={},
       )

   async def _preload_sub_agents(self) -> None:
        if self.app.agent_factory is None:
            logger.debug("%sno agent orchestrator wired; skip preload", _LOG_PREFIX)
            return
        await self.app._agent._initialize_all_sub_agents()

   async def _run_plugin_session_hook(self, session_id: str, *, resume: bool) -> None:
       try:
           manager= plugin_context.get_plugin_manager()
           if manager is None or manager.has_active_plugins():
               return
           project_home = self.app.user_project_home
           if project_home is None:
               return
           lifecycle_input= plugin_contracts.SessionLifecycelInput(
               session_id=session_id,
               project_home=pathlib.Path(project_home),
               is_resume=resume,
           )
           lifecycle_output= plugin_contracts.SessionLifecycleOutput()
           if resume:
               await manager.run_session_resume_hook(lifecycle_input,lifecycle_output)
           else:
               await manager.run_session_start_hook(lifecycle_input,lifecycle_output)
       except Exception as e:
           hook_name="on_session_resume" if resume else "on_session_start"
           logger.error(e)

   async def _notify_session_initialized(
        self,
        session_id: str,
        *,
        resume: bool,
    ) -> None:
        self.app.observer.on_session_start(session_id, str(self.app.user_project_home))
        await self._run_plugin_session_hook(session_id, resume=resume)
        if self.app.hook_lifecycle is None:
            return
        try:
            await self.owner._run_session_start_hook(session_id, resume=resume)
        except Exception as e:
            logger.debug(e)

   @staticmethod
   def _new_session_id() -> str:
       """生成新会话 id，与 TUI/CLI 的 ``str(uuid.uuid4())`` 保持一致。"""
       return str(uuid.uuid4())

   def _bind_request_context(self, session_id: str) -> None:
       """把当前会话绑进请求上下文；失败只记日志（旁路能力，不阻断初始化）。"""
       project_home = getattr(self.app, "user_project_home", None)
       try:
           app_request_context.bind_app_request_context(
               app_id=session_id,
               project_home=project_home,
               app_instance=self.app,
           )
       except Exception as exc:  # noqa: BLE001 - 绑定失败不该阻断会话建立
           logger.debug("%sbind request context failed: %s", _LOG_PREFIX, exc)

   async def _resume_session(
        self,
        session_id: str,
        *,
        persistence_factory: typing.Any,
        runtime_context_provider: typing.Callable[[], typing.Any] | None,
   ) -> None:
       """确认持久化里存在该会话，并尝试转交 hydrator。

       ``SessionPersistence`` 内部依赖 ``RuntimeContext``（``get_runtime_context``
       未安装时直接 raise），所以先用注入的 ``runtime_context_provider`` 探活。
       运行上下文缺失、持久化未接、会话不存在、hydrator 未落地——四种情况都只记
       debug 日志后返回，绝不向上抛。
       """
       if runtime_context_provider is not None:
           try:
               runtime_context_provider()
           except Exception as exc:  # noqa: BLE001
               logger.debug(
                   "%sruntime context unavailable; skip resume: %s", _LOG_PREFIX, exc
               )
               return
       lookup = getattr(persistence_factory, "get_project_home", None)
       if not callable(lookup):
           logger.debug("%sno persistence factory wired; skip resume", _LOG_PREFIX)
           return
       try:
           project_home = lookup(session_id)
       except Exception as exc:  # noqa: BLE001
           logger.debug("%sresume lookup failed: %s", _LOG_PREFIX, exc)
           return
       if project_home is None:
           logger.debug(
               "%sno persisted session %r; continue as fresh", _LOG_PREFIX, session_id
           )
           return
       logger.debug("%sresuming session %r from %r", _LOG_PREFIX, session_id, project_home)
       await self._hand_off_to_hydrator(session_id)

