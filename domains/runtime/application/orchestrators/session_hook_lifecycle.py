"""会话级 hook 生命周期协调器（SessionHookLifecycleCoordinator）。

``SessionOrchestrator`` 持有一个实例（构造时注入 ``metadata_factory`` 与
``save_app_state`` 两个协作者），把「一次会话的 hook 生命周期」收敛到这里：

- ``_dispath_session_start``（历史拼写保留）——会话启动编排：刷新 hook 组件
  → 初始化 hook 会话 → 派发 ``SessionStart`` → 派发 ``INSTRUCTIONS_LOADED``
  → 落一次应用状态；
- ``_run_session_start_hook`` / ``_run_instructions_loads_hooks`` —— 两个
  派发步骤的单独入口；
- ``end_hook_session`` —— 会话收尾（``rabbit_application.close_session`` /
  ``cleanup`` 在调，对应 ``SessionEnd`` 派发）；
- ``_refresh_hook_components`` / ``_hook_metadata`` / ``save_app_state_and_agents``
  —— 组件刷新、元数据构造、状态保存三个支撑动作。

三条铁律（与 ``session_hook_turns`` 一致）：

1. hook 是旁路能力——``app.hook_lifecycle`` 没装配、入口没上线、派发抛异常，
   一律降级，绝不让会话启动/收尾失败；
2. ``asyncio.CancelledError`` 必须透传；
3. 注入的协作者（``metadata_factory`` / ``save_app_state``）缺失只记日志，
   按「该动作没做」处理，不抛错。
"""

from __future__ import annotations

import asyncio
import logging
import typing
import domains.runtime.application.hook_lifecycle as hook_lifecycle_module
import domains.hook.domain.inputs as hook_inputs
import domains.hook.domain.runtime_models as hook_runtime_models
import domains.agent.domain.aggregates.react_agent as react_agent
logger = logging.getLogger(__name__)

__all__ = ["SessionHookLifecycleCoordinator"]

_LOG_PREFIX = "[SESSION-HOOK-LIFECYCLE] "

# 指令资源里 load_reason 的值（enum 的 .value；与 InstructionLoadReason.SESSION_START 对齐）。
# 不 import hook.domain.inputs：本模块对 hook 域只做鸭子类型消费。
_SESSION_START_REASON = "session_start"

# hook_lifecycle 上可能的「指令加载」派发入口（该入口尚未落地，落地后按其中一个
# 名字暴露即可被自动接上）。
_INSTRUCTIONS_ENTRY_POINTS: tuple[str, ...] = (
    "instructions_loaded",
    "instructions_loads",
    "instruction_loads",
)


class SessionHookLifecycleCoordinator:

    def __init__(
        self,
        app: typing.Any,
        *,
        metadata_factory: typing.Callable[..., typing.Any] | None = None,
        save_app_state: typing.Callable[..., typing.Any] | None = None,
    ) -> None:
        """:param app: 宿主应用（``RabbitApplication``）。
        :param metadata_factory: 元数据工厂（编排器的 ``_hook_metadata``，
          最终委托 ``SessionHookTurnCoordinator._hook_metadata``）；缺失时
          所有派发动作跳过。
        :param save_app_state: 应用状态保存协作者（编排器的
          ``save_app_state_and_agents``）；缺失时保存动作跳过。
        """
        self._app = app
        self._metadata_factory = metadata_factory
        self._save_app_state = save_app_state

    # --- 元数据 ------------------------------------------------------------

    def _hook_metadata(
        self,
        session_id: str,
        *,
        correlation_id: str ,
        prompt_id: str | None = None,
        entry_agent: react_agent.ReActAgent = None,
        model_index: int | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
    ) -> hook_lifecycle_module.HookLifecycleMetadata:
        return  self._metadata_factory(
            session_id,
            correlation_id=correlation_id ,
            prompt_id=prompt_id,
            entry_agent=entry_agent,
            model_index=model_index,
            agent_id=agent_id,
            agent_type=agent_type,
        )

    async def save_app_state_and_agents(
        self,
        session_id: str,
    ) -> None:
       await self._save_app_state(session_id)

    # --- 会话启动编排 ------------------------------------------------------

    async def _dispatch_session_start(
        self,
        session_id: str,
        *,
        source: str = "startup",
        model: str | None = None,
        session_title: str | None = None,
    ) -> dict[str, typing.Any]:
        """会话启动的 hook 编排（历史拼写 ``dispath`` 保留）。

        顺序即依赖：刷新组件（配置可能刚变）→ 初始化 hook 会话（``SessionEnd``
        的 claim 依赖它登记的 lifecycle id）→ 派发 ``SessionStart`` → 派发
        ``INSTRUCTIONS_LOADED`` → 落一次应用状态（``SessionStart`` 的
        environment_file 可能改写 env overlay，需要落盘才能跨轮生效）。

        每一步都是 best-effort：单独失败只记日志，不影响后续步骤。返回本次
        编排的摘要（各步骤是否真的执行），供调用方日志与测试观测。
        """
        summary: dict[str, typing.Any] = {
            "session_id": session_id,
            "refreshed": 0,
            "lifecycle_id": None,
            "session_start": False,
            "instructions": 0,
            "saved": False,
        }
        summary["refreshed"] = await self._refresh_hook_components()
        lifecycle = getattr(self._app, "hook_lifecycle", None)
        if lifecycle is None:
            logger.debug("%sno hook_lifecycle wired; session start hooks skipped", _LOG_PREFIX)
            return summary
        summary["lifecycle_id"] = await self._initialize_hook_session(session_id)
        started = await self._run_session_start_hook(
            session_id, source=source, model=model, session_title=session_title
        )
        summary["session_start"] = started is not None
        summary["instructions"] = await self._run_instructions_loads_hooks(session_id)
        try:
            await self.save_app_state_and_agents(session_id)
            summary["saved"] = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 保存失败不阻断会话启动
            logger.debug("%ssave after session start failed: %s", _LOG_PREFIX, exc)
        return summary

    async def _run_session_start_hook(
        self,
        session_id: str,
        *,
        resume: bool,
    ) -> None:
        lifecycle = self._app.hook_lifecycle
        if lifecycle is None:
            return None
        lifecycle_id = await lifecycle.initialize_session(session_id)
        metadata = self._hook_metadata(session_id, correlation_id=f"session-start:{session_id}:{'resume'if resume else 'startup'}",
                                       entry_agent=self._app.root_agent)
        await self._refresh_hook_components(session_id, resume=resume)
        await self._dispath_session_start(lifecycle, metadata, session_id, resume=resume)
        await lifecycle.start_watching(metadata, lifecycle_id)

    async def _run_instructions_loads_hooks(self, session_id: str) -> int:
        """为会话启动时加载的指令资源派发 ``INSTRUCTIONS_LOADED``，返回派发条数。

        指令资源来自 ``app.config.system_resources.instruction_resources``；
        只取 ``load_reason`` 为 ``session_start`` 的（glob 触发的那种在会话
        中后期加载，不归这里）。派发入口按 ``_INSTRUCTIONS_ENTRY_POINTS`` 探测，
        逐个资源调用 ``entry(session_id, resource)``——入口尚未落地，当前恒返回 0。
        """
        lifecycle = getattr(self._app, "hook_lifecycle", None)
        if lifecycle is None:
            return 0
        entry_point = None
        for name in _INSTRUCTIONS_ENTRY_POINTS:
            candidate = getattr(lifecycle, name, None)
            if callable(candidate):
                entry_point = candidate
                break
        if entry_point is None:
            logger.debug("%sinstructions entry not wired; skip", _LOG_PREFIX)
            return 0
        dispatched = 0
        for resource in self._session_start_instructions():
            try:
                produced = entry_point(session_id, resource)
                if hasattr(produced, "__await__"):
                    await produced
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其余
                logger.debug("%sinstructions dispatch failed: %s", _LOG_PREFIX, exc)
                continue
            dispatched += 1
        return dispatched

    # --- 组件刷新 ----------------------------------------------------------

    async def _refresh_hook_components(self) -> int:
        """刷新 hook 配置组件，返回成功执行的刷新动作数。

        依次探（都存在才都会执行）：

        1. ``hook_lifecycle.refresh``（未来公开入口）；
        2. ``hook_lifecycle._configuration_refresher``（当前私有槽位）；
        3. ``system_resources.hook_configuration_refresher``；
        4. ``system_resources`` 上的组件上下文更新器——**两种拼写都探**
           （``hook_component_context_updater`` 是字段正名，
           ``hook_conponent_context_updater`` 是 composition 层的笔误）。

        每个动作单独 try/except：刷新是 best-effort，一个挂了不该挡住其它。
        """
        lifecycle = getattr(self._app, "hook_lifecycle", None)
        resources = self._system_resources()
        candidates: list[typing.Any] = [
            getattr(lifecycle, "refresh", None),
            getattr(lifecycle, "_configuration_refresher", None),
            getattr(resources, "hook_configuration_refresher", None),
            getattr(resources, "hook_component_context_updater", None),
            getattr(resources, "hook_conponent_context_updater", None),
        ]
        refreshed = 0
        for candidate in candidates:
            if not callable(candidate):
                continue
            try:
                produced = candidate()
                if hasattr(produced, "__await__"):
                    await produced
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - best-effort
                logger.debug("%srefresh action failed: %s", _LOG_PREFIX, exc)
                continue
            refreshed += 1
        return refreshed

    # --- 会话收尾 ----------------------------------------------------------

    async def end_hook_session(
        self,
        session_id: str,
        reason: hook_inputs.SessionEndReason,
        *,
        cwd:str|None=None,
    ) -> hook_runtime_models.HookDispatchResult|None:
        """结束会话的 hook 生命周期（派发 ``SessionEnd``），返回是否真的派发。

        ``rabbit_application.close_session`` / ``cleanup`` 经编排器调到这里。
        ``reason`` 收 ``hook_inputs.SessionEndReason`` 枚举（调用方传什么透什么）。
        没装配 / 没入口 / 派发失败一律返回 ``False``——收尾链路不能因为 hook
        半路炸掉而中断。
        """
        lifecycle = self._app.hook_lifecycle
        if lifecycle is None:
            logger.debug("%sno hook_lifecycle wired; session end skipped", _LOG_PREFIX)
            return None
        try:
            metadata = self._hook_metadata(
                session_id,
                correlation_id=f"session-end:{session_id}:{reason.value}",
                entry_agent= self._app.root_agent,
            )
            if cwd is not None:
                metadata= metadata.model_copy(update={"cwd": cwd})
            return await lifecycle.session_end(metadata, reason=reason)
        except Exception as exc:  # noqa: BLE001 - 收尾不阻断
            logger.debug("%ssession_end degraded: %s", _LOG_PREFIX, exc)
            return None

    # --- 内部探测 ----------------------------------------------------------

    async def _initialize_hook_session(self, session_id: str) -> str | None:
        """初始化 hook 会话（登记 lifecycle id）；失败返回 ``None``。

        ``SessionEnd`` 的 claim 依赖这次登记的 id，所以放在 ``SessionStart``
        之前。``initialize_session`` 内部还依赖未落地的 ``stop_watching``，
       今天调用会降级——不影响后续步骤。
        """
        lifecycle = getattr(self._app, "hook_lifecycle", None)
        initializer = getattr(lifecycle, "initialize_session", None)
        if not callable(initializer):
            return None
        try:
            produced = initializer(session_id)
            if hasattr(produced, "__await__"):
                produced = await produced
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.debug("%sinitialize_session degraded: %s", _LOG_PREFIX, exc)
            return None
        return produced if isinstance(produced, str) else None

    def _default_agent(self) -> typing.Any:
        """探默认 agent 运行时：``agent_runtimes`` → ``root_agent``。

        只读已登记运行时，不现场构造（``build_agent_for_invoke`` 有副作用，
        元数据构造不值得为它造一个 agent）。
        """
        runtimes = getattr(self._app, "agent_runtimes", None)
        if isinstance(runtimes, dict):
            default_id = getattr(self._app, "current_default_agent_id", None)
            if isinstance(default_id, str) and default_id in runtimes:
                return runtimes[default_id]
        root_agent = getattr(self._app, "root_agent", None)
        if root_agent is not None:
            return root_agent
        return None

    def _model_name(self) -> str | None:
        """探当前模型名：运行时 ``model.model_name`` → 实例 ``spec.model``。"""
        agent = self._default_agent()
        name = getattr(getattr(agent, "model", None), "model_name", None)
        if isinstance(name, str) and name:
            return name
        spec = getattr(agent, "spec", None)
        name = getattr(spec, "model", None)
        if isinstance(name, str) and name:
            return name
        return None

    def _session_title(self) -> str | None:
        """当前会话主题（``SessionStart`` 的 title 缺省值）。"""
        title = getattr(self._app, "topic", None)
        return title if isinstance(title, str) and title.strip() else None

    def _system_resources(self) -> typing.Any:
        config = getattr(self._app, "config", None)
        return getattr(config, "system_resources", None)

    def _session_start_instructions(self) -> list[typing.Any]:
        """会话启动时加载的指令资源（``load_reason`` 为 ``session_start``）。"""
        resources = getattr(self._system_resources(), "instruction_resources", None)
        if not isinstance(resources, (list, tuple)):
            return []
        selected: list[typing.Any] = []
        for resource in resources:
            reason = getattr(resource, "load_reason", None)
            value = getattr(reason, "value", reason)
            if value == _SESSION_START_REASON:
                selected.append(resource)
        return selected
