"""Agent 启动引导服务。

职责：在「配置已加载」与「agent 实例已创建」之间完成引导工作——注册 agent
到运行时容器、绑定角色上下文、写入初始快照等。

``AgentOrchestrator`` 构造时持有一个实例。当前阶段只保证可构造与可降级：
引导失败（依赖缺失、容器未就绪）只记录 warning，由上层决定后续动作。

:meth:`AgentBootstrapService.execute` 是 agent 装配的主流水线，由
``AgentOrchestrator._create_agents`` 调用。它把「配置 → 运行时依赖 → 按模式
创建 → 登记回宿主」串成一条链，任何一步失败都只降级不抛出——装配失败不应
让整个应用起不来，用户至少还能看到界面并得到提示。
"""

from __future__ import annotations

import collections.abc
import inspect
import logging
import pathlib
import typing

import domains.permission.composition as authorization_composition
import domains.runtime.application.orchestrators.agent_orchestration_contacts as orchestration_contracts
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.agent.application.loader.agent_factory as agent_factory_module
import domains.agent.application.loader.root_agent_loader as root_agent_loader_module
import domains.mcp.infrastructure.client_runtime as client_runtime
import domains.plugin.application.request_context as plugin_request_context
from domains.mcp.domain import tool_projection, server_configuration
import domains.plugin.domain as plugin_contracts

MCPProjectionResolver = collections.abc.Callable[
    [collections.abc.Collection[str]],
    tool_projection.MCPToolProjectionMode,
]

AgentFactory=agent_factory_module.AgentFactory
_MCP_TOOL_GATEWAY_PLUGIN_NAME="mcp_tool_gateway"
logger = logging.getLogger(__name__)

__all__ = ["AgentBootstrapService"]

def _resolve_mcp_tool_projection_mode(
    active_plugin_names: typing.Collection[str],
)-> tool_projection.MCPToolProjectionMode:
    if _MCP_TOOL_GATEWAY_PLUGIN_NAME in active_plugin_names:
        return tool_projection.MCPToolProjectionMode.GATEWAY
    return tool_projection.MCPToolProjectionMode.EAGER


class AgentBootstrapService:
    """引导 agent 进入运行状态。"""

    # --- 主流水线 ---------------------------------------------------------

    async def execute(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        sub_agent_configs: collections.abc.Sequence[agent_runtime_module.AgentRuntime],
        root_agent_config: root_agent_config_module.RootAgentConfig,
        *,
        agent_factory_type: type[agent_factory_module.AgentFactory],
        mcp_manager_type: type[client_runtime.MCPManager],
        projection_resolver: MCPProjectionResolver,
        on_progress: orchestration_contracts.ProcessCallback|None = None,
        defer_root_creation: bool = False,
    ) -> None:
        """执行 agent 装配流水线。

        :param orchestrator: 调用方编排器（``AgentOrchestrator``）；通过其
            ``app`` 属性拿到宿主应用。若传入对象本身没有 ``app``，则把它当作
            宿主应用处理（兼容直接传 app 的调用点）。
        :param sub_agent_configs: 子 agent 配置；缺失时回落到加载服务。
        :param root_agent_config: 根 agent 配置；调用点可能误传**类**本身，
            会在配置解析阶段被拦掉。
        :param agent_factory_type: agent 工厂类型，用于实例化并挂到宿主。
        :param mcp_manager_type: MCP 管理器类型，用于实例化并挂到宿主。
        :param projection_resolver: 工具投影模式解析器，接收「已启用插件名集合」。
        :param on_process: 进度回调；签名不固定，由编排器负责适配。
        :param defer_root_creation: 为真时本轮**不创建**根 agent（留给后续阶段）。
        """
        await host.app.ensure_plugins_initialized()
        plugin_request_context.set_plugin_manager(host.app.plugin_manager)
        await self._configure_mcp_runtime(
            host,
            mcp_manager_type=mcp_manager_type,
            projection_resolver=projection_resolver,
            on_progress=on_progress,
        )
        sub_agents= await self._build_sub_agent_blueprints(
            host,
            sub_agent_configs
        )

        await self._report(on_progress, "Building agent factory ...")
        mcp_manager= host.app.mcp_manager
        if mcp_manager is None:
            raise RuntimeError("mCP RUNTIME MUST BE CONFIGED")
        agent_factory=agent_factory_type(
            mcp_manager= mcp_manager,
            config_manager = host.app.config_manager,
            app_instance= typing.cast(
                agent_factory_module.AgentFactoryApplicationPort,
                host.app,
            ),
            skill_registry= host._system_skill_registry(),
            skill_registry_refresher= host._system_skill_registry_refresher(),
            rabbit_skills_md= host._system_rabbit_skills_md(),
            instruction_resources= host._system_instruction_resources(),
        )
        host.app.agent_factory = agent_factory
        host.app.root_agent_config = root_agent_config
        host._ensure_agent_config_snapshots(
            sub_agents=sub_agents,
            root_agent_config=root_agent_config,
            agent_factory=agent_factory,
        )
        snapshot_sub_agents= host._explicit_runtimes_from_snapshots()
        if snapshot_sub_agents:
            sub_agents= snapshot_sub_agents
        host.app.sub_agents = sub_agents
        host.app.sub_agent_configs_mcp.clear()
        for sub_agent in sub_agents:
           host.app.sub_agent_configs_mcp[sub_agent.name] = sub_agent
        if defer_root_creation:
            host.app.agent_lifecycle=None
            host._install_subtask_auto_fix_hook()
            return

        await self._report(on_progress,"Creating root agent ...")
        role_sub_agents, utility_sub_agents = self._partition_skills(sub_agents)
        host.app.root_agent = await host._build_root_agent_for_mode(
            app_mode = host.app.app_mode,
            role_sub_agents = role_sub_agents,
            utility_sub_agents = utility_sub_agents,
            root_agent_config = root_agent_config,
            agent_factory = agent_factory,
            on_progress = on_progress,
        )
        if host.app.root_agent is None:
            raise RuntimeError(f"Mode '{host.app.app_mode}' produced no root agent")
        host.app.current_default_agent_id = host.app.root_agent.name
        host.app.agent_lifecycle=None
        host._install_subtask_auto_fix_hook()

    # --- MCP 运行时 -------------------------------------------------------

    async def _configure_mcp_runtime(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        *,
        mcp_manager_type: type[client_runtime.MCPManager],
        projection_resolver: MCPProjectionResolver | None = None,
        on_progress: orchestration_contracts.ProcessCallback|None = None,
    ) -> None:
        """配置 MCP 运行时，并把管理器挂到 ``host.app.mcp_manager``。

        MCP 是 agent 的外部工具来源：紧接着的 agent 工厂要用同一个
        ``mcp_manager`` 构造 agent，所以这里必须把它**真正**建好并回写宿主。
        拿不到就返回 ``None``——``execute`` 里那句显式检查
        （``mCP RUNTIME MUST BE CONFIGED``）会把它变成可诊断的失败，比在这里
        吞掉一个模糊异常要好定位得多。

        本方法只降级不抛出：MCP 服务连不上、管理器没有启动能力、配置缺失等
        情况都只记日志，保证「没有 MCP 也能起应用」。

        :param host: 编排宿主；经其 ``app`` 拿宿主应用。
        :param mcp_manager_type: MCP 管理器类型。
        :param on_process: 进度回调；签名不固定，按多种形态依次尝试。
        :param projection_resolver: 工具投影模式解析器（可选）。
        :return: 已挂到宿主的 MCP 管理器；失败时为 ``None``。
        """
        # 这里**不**走 ``_host_of`` 的「把 host 当宿主」回退：MCP 配置来自
        # ``app.config`` / ``app.config_manager``，且 ``execute`` 随后直接读
        # ``host.app.mcp_manager``，因此宿主应用必须是真实存在的对象。
        runtime_configs = self._runtime_mcp_configs(host)
        project_mode = projection_resolver(host.app.plugin_manager.get_active_plugin_names())
        host.app.mcp_manager = mcp_manager_type(
            runtime_configs= runtime_configs,
            authorization_service = authorization_composition.get_authorization_service(),
            elicitation_handler = host.app._session_orch,
            tool_projection_mode = project_mode,
        )
        mcp_count= len(host.app.mcp_manager.mcp_registry.list_all())
        if mcp_count >0:
            await self._report(on_progress,"Configuring MCP runtime ...")

    @staticmethod
    def _runtime_mcp_configs(
            host: orchestration_contracts.AgentOrchestrationHost,
    )->list[server_configuration.MCPServerConfig]:
        system_resources = host.app.config.system_resources
        if system_resources is not None:
            return [
                resource.model_copy(deep=True)
                if isinstance(resource, server_configuration.MCPServerConfig)
                else server_configuration.MCPServerConfig.model_validate(
                    resource,
                    from_attributes=True,
                )
                for resource in system_resources.mcp_servers
            ]
        configured = host.app.config.mcp_servers
        return list(configured) if configured  is not None else []

    @staticmethod
    def _mcp_server_configs(app: typing.Any) -> list[typing.Any]:
        """收集宿主上的 MCP 服务配置。

        配置的挂载位置在不同装配路径下并不统一（有的在 ``AppConfig.mcp_servers``，
        有的在 ``system_resources`` 下，有的直接挂在 app 上），这里按优先级依次
        取第一个非空来源。取不到返回空列表——没有 MCP 服务是合法状态。

        配置项**原样透传**：它们可能是 dict、``MCPServerResource`` 或别的模型，
        在这层做归一化反而可能丢字段（当前 ``MCPServerConfig`` 还没有字段定义，
        ``from_dict`` 会把内容全部丢掉），归一化交给 manager 自己处理。
        """
        sources = (
            lambda: getattr(app, "mcp_server_configs", None),
            lambda: getattr(getattr(app, "config", None), "mcp_servers", None),
            lambda: getattr(
                getattr(getattr(app, "config", None), "system_resources", None),
                "mcp_servers",
                None,
            ),
            lambda: getattr(app, "mcp_servers", None),
        )
        for source in sources:
            try:
                value = source()
            except Exception:
                continue
            if isinstance(value, typing.Iterable) and not isinstance(value, (str, bytes)):
                configs = [item for item in value if item is not None]
                if configs:
                    return configs
        return []

    @staticmethod
    def _construct_mcp_manager(
        mcp_manager_type: typing.Any,
        app: typing.Any,
        server_configs: typing.Sequence[typing.Any],
    ) -> typing.Any:
        """按多种可能的构造签名创建 MCP 管理器。

        管理器目前还是空壳，构造签名尚未定型，所以按「信息量从多到少」依次
        尝试：完整关键字 → 仅服务配置 → 位置参数 → 仅配置管理器 → 宿主 → 无参。
        ``TypeError`` 视为签名不匹配继续试下一种；其它异常视为真实故障，记录
        后直接放弃（避免把内部错误误判成签名问题而反复重试）。
        """
        if mcp_manager_type is None or not callable(mcp_manager_type):
            return None
        config_manager = getattr(app, "config_manager", None)
        observer = getattr(app, "observer", None)
        attempts = (
            lambda: mcp_manager_type(
                server_configs=server_configs,
                config_manager=config_manager,
                app_instance=app,
                observer=observer,
            ),
            lambda: mcp_manager_type(server_configs=server_configs),
            lambda: mcp_manager_type(server_configs),
            lambda: mcp_manager_type(config_manager=config_manager),
            lambda: mcp_manager_type(app),
            lambda: mcp_manager_type(),
        )
        for attempt in attempts:
            try:
                manager = attempt()
            except TypeError:
                continue
            except Exception:
                logger.exception("[AGENT-BOOTSTRAP] MCP manager construction failed")
                return None
            if manager is not None:
                return manager
        return None

    async def _start_mcp_manager(self, manager: typing.Any) -> None:
        """启动 MCP 管理器（建立连接/装载工具）。

        不同实现的启动入口名字不同，按 ``start`` / ``connect`` / ``initialize``
        依次取第一个存在的调用。启动失败只记日志——MCP 不可用不应阻断应用启动。
        """
        for name in ("start", "connect", "initialize"):
            method = getattr(manager, name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("[AGENT-BOOTSTRAP] MCP manager %s() failed", name)
            return

    # --- 流水线各步骤 -----------------------------------------------------

    @staticmethod
    def _host_of(orchestrator: typing.Any) -> typing.Any:
        """从编排器取宿主应用；拿不到时把编排器本身当宿主。"""
        app = getattr(orchestrator, "app", None)
        return app if app is not None else orchestrator

    def _resolve_configs(
        self,
        orchestrator: typing.Any,
        sub_agent_configs: typing.Any,
        root_agent_config: typing.Any,
    ) -> tuple[list[typing.Any], typing.Any]:
        """解析本轮要用的配置；编排器有解析能力时优先复用。"""
        resolver = getattr(orchestrator, "_resolve_agent_configs", None)
        if callable(resolver):
            try:
                sub_configs, root_config = resolver(sub_agent_configs, root_agent_config)
                return list(sub_configs or ()), root_config
            except Exception:
                logger.exception("[AGENT-BOOTSTRAP] resolve agent configs failed, falling back")

        sub_configs = (
            list(sub_agent_configs) if isinstance(sub_agent_configs, typing.Iterable) else []
        )
        root_config = root_agent_config if not isinstance(root_agent_config, type) else None
        return sub_configs, root_config

    @staticmethod
    def _store_configs(
        orchestrator: typing.Any,
        app: typing.Any,
        sub_configs: typing.Sequence[typing.Any],
        root_config: typing.Any,
    ) -> None:
        """把配置回写宿主；回写失败不影响后续装配。"""
        store = getattr(orchestrator, "_store_agent_configs", None)
        if not callable(store):
            return
        try:
            store(app, sub_configs, root_config)
        except Exception:
            logger.exception("[AGENT-BOOTSTRAP] store agent configs failed")

    @staticmethod
    def _mode_of(orchestrator: typing.Any, app: typing.Any) -> str:
        """读取当前应用模式；读不到按 ``delegate`` 处理。"""
        reader = getattr(orchestrator, "_current_mode", None)
        if callable(reader):
            try:
                return str(reader(app))
            except Exception:
                logger.exception("[AGENT-BOOTSTRAP] read app mode failed")
        mode = getattr(app, "app_mode", None)
        return str(mode).strip().lower() if isinstance(mode, str) and mode.strip() else "delegate"

    def _ensure_mcp_manager(self, app: typing.Any, mcp_manager_type: typing.Any) -> typing.Any:
        """确保宿主上有一个 MCP 管理器；已存在则直接复用。"""
        existing = getattr(app, "mcp_manager", None)
        if existing is not None:
            return existing
        if mcp_manager_type is None:
            return None
        manager = self._instantiate(mcp_manager_type, app)
        if manager is None:
            logger.warning("[AGENT-BOOTSTRAP] MCP manager is unavailable; MCP tools are disabled")
            return None
        try:
            app.mcp_manager = manager
        except Exception:
            logger.exception("[AGENT-BOOTSTRAP] cannot attach MCP manager to host")
        return manager

    def _ensure_agent_factory(self, app: typing.Any, agent_factory_type: typing.Any) -> typing.Any:
        """确保宿主上有一个 agent 工厂；已存在则直接复用。"""
        existing = getattr(app, "agent_factory", None)
        if existing is not None:
            return existing
        if agent_factory_type is None:
            return None
        factory = self._instantiate(agent_factory_type, app)
        if factory is None:
            logger.warning("[AGENT-BOOTSTRAP] agent factory is unavailable")
            return None
        try:
            app.agent_factory = factory
        except Exception:
            logger.exception("[AGENT-BOOTSTRAP] cannot attach agent factory to host")
        return factory

    @staticmethod
    def _instantiate(factory_type: typing.Any, app: typing.Any) -> typing.Any:
        """按「带宿主 → 无参」两种签名尝试实例化。

        工厂/管理器的构造签名在本项目里没有统一约定（有的拿宿主，有的纯无参），
        这里依次尝试，都失败返回 ``None`` 由调用方降级。
        """
        if factory_type is None or not callable(factory_type):
            return None
        for arguments in ((app,), ()):
            try:
                return factory_type(*arguments)
            except TypeError:
                continue
            except Exception:
                logger.exception("[AGENT-BOOTSTRAP] cannot instantiate %r", factory_type)
                return None
        return None

    def _resolve_projection(self, app: typing.Any, projection_resolver: typing.Any) -> typing.Any:
        """解析 MCP 工具投影模式，并把结果挂到宿主供后续消费。"""
        if projection_resolver is None:
            return None
        if not callable(projection_resolver):
            logger.warning("[AGENT-BOOTSTRAP] projection resolver is not callable, ignored")
            return None
        try:
            mode = projection_resolver(self._active_plugin_names(app))
        except Exception:
            logger.exception("[AGENT-BOOTSTRAP] resolve MCP tool projection mode failed")
            return None
        try:
            app.mcp_tool_projection_mode = mode
        except Exception:
            logger.debug("[AGENT-BOOTSTRAP] cannot attach projection mode to host", exc_info=True)
        return mode

    @staticmethod
    def _active_plugin_names(app: typing.Any) -> tuple[str, ...]:
        """读取已启用插件名；读不到返回空元组（解析器退到默认模式）。"""
        plugin_manager = getattr(app, "plugin_manager", None)
        if plugin_manager is None:
            return ()
        for attr in ("enabled_names", "names"):
            candidate = getattr(plugin_manager, attr, None)
            try:
                value = candidate() if callable(candidate) else candidate
            except Exception:
                continue
            if isinstance(value, typing.Iterable) and not isinstance(value, (str, bytes)):
                return tuple(str(name) for name in value)
        return ()

    async def _create_agents(
        self,
        sub_agent_configs: typing.Sequence[agent_runtime_module.AgentRuntime],
        root_agent_configs: root_agent_config_module.RootAgentConfig,
        on_progress: orchestration_contracts.ProgressCallback|None = None,
        *,
        defer_root_creation: bool=False,
    ) -> None:
        """按模式创建 agent；编排器无该能力时返回空列表。

        修改前
        ------
        1. **漏传 ``mcp_manager_type``**：``execute`` 的 keyword-only 参数里
           必填它，缺失直接 ``TypeError``；上游 ``TUIApplication.run`` 用大
           ``try/except`` 包着，异常被记一条 warning 就吞掉，于是「TUI 起得来、
           能敲命令，但一问就再无响应」——agent 压根没被创建。
        2. **关键字写错**：传的是 ``root_agent_configs=``，而 ``execute`` 的
           形参叫 ``root_agent_config``（单数），即使补上第1 条也会
           ``TypeError: unexpected keyword argument``。
        """
        await self._bootstrap.execute(
            self,
            sub_agent_configs=sub_agent_configs,
            root_agent_config=root_agent_configs,
            agent_factory_type= AgentFactory,
            mcp_manager_type= client_runtime.MCPManager,
            projection_resolver=_resolve_mcp_tool_projection_mode,
            on_progress=on_progress,
            defer_root_creation=defer_root_creation,
        )

    def _create_with_factory(
        self,
        factory: typing.Any,
        sub_configs: typing.Sequence[typing.Any],
        root_config: typing.Any,
    ) -> list[typing.Any]:
        """模式化创建没有产出时，退到工厂直接构造。

        工厂的方法名同样没有统一约定，按 ``create`` / ``create_agent`` /
        ``build`` 依次尝试，任一可用即采用；异步工厂不在同步流水线上等待，
        只记录后跳过。
        """
        for method_name in ("create", "create_agent", "build"):
            method = getattr(factory, method_name, None)
            if not callable(method):
                continue
            try:
                try:
                    result = method(configs=sub_configs, root_agent_config=root_config)
                except TypeError:
                    result = method(sub_configs)
            except Exception:
                logger.exception("[AGENT-BOOTSTRAP] factory %s() failed", method_name)
                continue
            if inspect.isawaitable(result):
                logger.debug("[AGENT-BOOTSTRAP] factory %s() is async, skipped", method_name)
                continue
            if isinstance(result, typing.Iterable) and not isinstance(result, (str, bytes)):
                created = [agent for agent in result if agent is not None]
                if created:
                    return created
        return []

    @staticmethod
    def _register(
        orchestrator: typing.Any,
        app: typing.Any,
        agents: typing.Sequence[typing.Any],
    ) -> str | None:
        """把 agent 登记到宿主容器，返回默认 agent 标识。"""
        register = getattr(orchestrator, "_register_created_agents", None)
        if not callable(register):
            return None
        try:
            return register(app, agents)
        except Exception:
            logger.exception("[AGENT-BOOTSTRAP] register created agents failed")
            return None

    async def _report(
        self,
        on_progress: typing.Any,
        message: str,
        **payload: object,
    ) -> None:
        """向进度回调汇报一条消息。

        调用点（``execute`` / ``_configure_mcp_runtime``）只保证「给一个回调和
        一句话」，回调本身的签名没有约定，因此按「纯文本 → 结构化字典 → 无参」
        依次尝试，命中即止。进度汇报不是主链路：回调报错只记日志，不能因为它
        把装配搞挂。
        """
        if on_progress is None:
            return
        if not callable(on_progress):
            logger.warning("[AGENT-BOOTSTRAP] on_process is not callable, ignored")
            return
        structured = {"message": message, **payload}
        for call in (
            lambda: on_progress(message),
            lambda: on_progress(structured),
            lambda: on_progress(),
        ):
            try:
                result = call()
                # 协程要 await 才会真正执行（进而才会抛错），所以等待也在保护范围内
                if inspect.isawaitable(result):
                    result = await result
            except TypeError:
                continue
            except Exception:
                logger.exception("[AGENT-BOOTSTRAP] progress report failed: %s", message)
                return
            return
        logger.debug("[AGENT-BOOTSTRAP] on_process signature not matched: %s", message)

    # --- 既有引导入口 -----------------------------------------------------

    def bootstrap(
        self,
        app: typing.Any = None,
        *,
        agent_configs: typing.Iterable[typing.Any] | None = None,
        root_agent_config: typing.Any = None,
    ) -> list[typing.Any]:
        """引导一批 agent。

        :param app: 宿主应用；``None`` 时无法引导，直接返回空列表。
        :param agent_configs: 待引导的 agent 配置。
        :param root_agent_config: 根 agent 配置（可选）。
        :return: 实际引导成功的 agent 列表；失败时为空列表。
        """
        if app is None:
            logger.warning("agent bootstrap skipped: host application is unavailable")
            return []
        configs = list(agent_configs or ())
        logger.debug("agent bootstrap requested for %d config(s)", len(configs))
        return []

    def __repr__(self) -> str:
        return "AgentBootstrapService()"

    async def _build_sub_agent_blueprints(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        configs: collections.abc.Sequence[agent_runtime_module.AgentRuntime],
     )->list[agent_runtime_module.AgentRuntime]:
        blueprints: list[agent_runtime_module.AgentRuntime]=[]
        for config in configs:
            slot = host._resolve_skill_agent_slot(config)
            system_prompt = await self._transform_sub_agent_prompt(
                host,
                config,
                slot,
            )
            blueprints.append(
                config.model_copy(
                    deep=True,
                    update={
                        "system_prompt": system_prompt,
                        "llm_config": host.app.llm_config,
                        "agent_slot": slot,
                    },
                )
            )
        return blueprints

    # -- 子 agent 提示词 ---------------------------------------------------

    async def _transform_sub_agent_prompt(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        config: agent_runtime_module.AgentRuntime,
        slot: plugin_contracts.AgentSlot | None,
    ) -> str:
        """产出一个子 agent 的最终系统提示词。

        基础文本按「命中即止」的顺序解析：

        1. ``config.system_prompt`` —— 专家卡自带的提示词，最权威；
        2. ``slot.system_prompt_file`` —— 模式声明给该槽位指定的提示词文件，
           相对宿主的 ``root_agent_prompts_dir`` 解析（未配置时退回项目根）；
        3. ``config.description`` / ``slot.description`` —— 说明文本顶上；
        4. ``root_agent_loader.DEFAULT_SYSTEM_PROMPT`` —— 内置兜底。

        拿到基础文本后再过一遍插件的 ``system_transform`` 贡献
        （见 :mod:`agent.service.plugin_prompt_transform`）。插件体系未就绪、
        槽位关掉该类贡献、或插件本身抛异常，都只让这段变换不生效，提示词
        原样返回——**提示词缺失不该让装配失败**。

        :param host: 编排宿主；经其 ``app`` 取提示词目录与插件管理器。
        :param config: 子 agent 配置（``AgentRuntime``）。
        :param slot: 该配置解析出的槽位声明；``None`` 表示未命中槽位。
        :return: 系统提示词文本，不会为 ``None``。
        """
        system_prompt = config.system_prompt
        if host.app.project_system_prompt:
            system_prompt = (
                f"{system_prompt}\n\n---\n"
                "**IMPORTANT PROJECT-LEVEL GUIDELINES**\n"
                "in addition to your primary task, you must strictly adhere"
                "to the following project-level guidelines. in all your actions:\n\n"
                f"{host.app.project_system_prompt}"
            )
        plugin_manager= plugin_request_context.get_plugin_manager()
        if plugin_manager is None or not plugin_manager.has_active_plugins():
            return system_prompt
        try:
            transform_input= plugin_contracts.SystemTransformInput(
                system=(system_prompt,),
                agent_name=config.name,
                agent_role="sub_agent",
            )
            transform_output= plugin_contracts.SystemTransformOutput(system=[system_prompt])
            await plugin_manager.run_system_transform_hook(
                transform_input,
                transform_output,
                slot=slot,
            )
        except Exception as exc:
            logger.exception(exc)
            return system_prompt
        return "\n\n".join(transform_output.system) if transform_output.system else system_prompt

    @staticmethod
    def _resolve_sub_agent_prompt(
        host: orchestration_contracts.AgentOrchestrationHost,
        config: typing.Any,
        slot: typing.Any | None,
    ) -> str:
        """按优先级取出子 agent 的基础提示词文本。"""
        for candidate in (
            str(getattr(config, "system_prompt", "") or ""),
            AgentBootstrapService._read_slot_prompt_file(host, slot),
            str(getattr(config, "description", "") or ""),
            str(getattr(slot, "description", "") or "") if slot is not None else "",
        ):
            if candidate.strip():
                return candidate.strip()
        logger.debug(
            "no system prompt resolved for sub agent %r; using built-in default",
            AgentBootstrapService._sub_agent_name(config, slot),
        )
        return root_agent_loader_module.DEFAULT_SYSTEM_PROMPT

    @staticmethod
    def _read_slot_prompt_file(
        host: orchestration_contracts.AgentOrchestrationHost,
        slot: typing.Any | None,
    ) -> str:
        """读槽位声明的提示词文件；不可读返回空串（交给下一级兜底）。"""
        prompt_file = str(getattr(slot, "system_prompt_file", "") or "") if slot is not None else ""
        if not prompt_file:
            return ""
        path = pathlib.Path(prompt_file)
        if not path.is_absolute():
            base = AgentBootstrapService._prompts_dir_of(host)
            if base is not None:
                path = base / path
        try:
            return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            logger.debug("sub agent prompt file unreadable: %s", path, exc_info=True)
            return ""

    @staticmethod
    def _prompts_dir_of(
        host: orchestration_contracts.AgentOrchestrationHost,
    ) -> pathlib.Path | None:
        """提示词文件相对路径的解析基准目录。"""
        app = getattr(host, "app", None)
        for value in (
            getattr(app, "root_agent_prompts_dir", None),
            getattr(app, "user_project_home", None),
            getattr(app, "project_home", None),
        ):
            if not value:
                continue
            try:
                return pathlib.Path(str(value))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _sub_agent_name(config: typing.Any, slot: typing.Any | None) -> str:
        """子 agent 的显示名：槽位名优先，其次配置名。"""
        slot_name = str(getattr(slot, "name", "") or "") if slot is not None else ""
        if slot_name:
            return slot_name
        return str(
            getattr(config, "name", "")
            or getattr(config, "agent_id", "")
            or ""
        )

    @staticmethod
    def _plugin_manager_of(
        host: orchestration_contracts.AgentOrchestrationHost,
    ) -> typing.Any | None:
        """取插件管理器：宿主属性优先，其次请求上下文。"""
        manager = getattr(getattr(host, "app", None), "plugin_manager", None)
        if manager is not None:
            return manager
        try:
            return plugin_request_context.get_plugin_manager()
        except Exception:
            logger.debug("plugin manager unavailable from request context", exc_info=True)
            return None

    @staticmethod
    def _partition_skills(
        sub_agents:list[agent_runtime_module.AgentRuntime],
    )->tuple[
        list[agent_runtime_module.AgentRuntime],
        list[agent_runtime_module.AgentRuntime],
    ]:
        role_sub_agents= [agent for agent in sub_agents if agent.skill_type=="role"]
        utility_sub_agents = [agent for agent in sub_agents if agent.skill_type=="utility"]
        return role_sub_agents, utility_sub_agents
