"""群聊场景下的 Agent 构建服务。

群聊（``groupchat``）与其它模式的根本差异：多个 agent 处于**对等**关系，
共享同一个会话板，消息按广播/点名规则投递，而不是「主 agent 委托子 agent」。
因此群聊的拓扑不是「一棵根 agent 树」，而是「一个协调用的编排者 + 一组对等
参与者」：编排者负责决定谁在什么时候发言，参与者之间不存在上下级。

本类的产物就是这个**编排者 agent**（``ReActAgent``）。它与
``mode_agent_creation.ModeAgentCreationService`` 的分工：

- 通用路径：按槽位用 ``AgentFactory`` 直接造一个 agent，槽位之间是并列关系，
  由调用方（``_interpret_mode``）收集起来选根；
- 群聊路径：模式声明把根槽位的 ``agent_type`` 标成 ``orchestrator``，
  ``mode_agent_creation`` 据此把控制权交给
  ``AgentOrchestrator._build_orchestrator_agent``，也就是本类。

取名沿用既有约定：编排者用**根槽位名**（``ModeDeclaration.root_agent``），
配置名为辅；参与者名单来自 ``sub_agents``——群聊里它们全部可见，不做
``subagent_visibility`` 裁剪，因为「谁对谁可见」是发言顺序问题，不是可见性问题。

失败策略：本类**不降级为空**。群聊缺了编排者就等于没有群聊，退化成一个空 agent
只会让问题延后到第一轮对话才暴露，且丢掉了「工厂不可用」这个真正的原因。所以
构建不出时抛 ``RuntimeError`` 并写明失败在哪一步——这比上游那句笼统的
``root_agent is None`` 好定位。
"""

from __future__ import annotations

import inspect
import logging
import typing

import domains.agent.domain.entities.agent_role as agent_role_module
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.runtime.application.orchestrators.agent_orchestration_contacts as orchestration_contracts
import domains.agent.application.loader.agent_factory as agent_factory_module
import domains.plugin.application.request_context as plugin_request_context
import domains.plugin.domain as plugin_contracts
import domains.agent.domain.aggregates.react_agent as agent_module

logger = logging.getLogger(__name__)

__all__ = ["GroupChatAgentBuilder"]

#: 编排者槽位的构建形态；``mode_agent_creation`` 按它把控制权交到本类。
_ORCHESTRATOR_AGENT_TYPE = "orchestrator"

#: 编排者没有专属槽位名时的兜底名，与 ``AgentOrchestrator._current_mode``
#: 的默认根槽位名保持一致。
_FALLBACK_ORCHESTRATOR_NAME = "planner"

_LOG_PREFIX = "[GROUPCHAT-BUILD] "

#: ``utility_tool_names`` 的元素形状：(参与者名, 工具名)。
UtilityToolRef = tuple[str, str]


class GroupChatAgentBuilder:
    """构建群聊拓扑中的编排者 agent。

    无参构造：依赖（工厂、根配置、参与者名单）全部由 ``build`` 的入参提供。
    这样本类可以被 ``AgentOrchestrator`` 在构造期就实例化并持有，而不必等
    宿主应用装配完成——那时工厂还不存在。
    """

    __slots__ = ()

    def __init__(self) -> None:
        """无参构造：依赖全部由调用入参提供。"""

    # --- 主入口 -------------------------------------------------------------

    async def build(
        self,
        host: orchestration_contracts.AgentOrchestrationHost,
        *,
        agent_factory: agent_factory_module.AgentFactory | None = None,
        root_agent_config: root_agent_config_module.RootAgentConfig | None = None,
        sub_agents: typing.Sequence[agent_runtime_module.AgentRuntime] | None = None,
        on_progress: orchestration_contracts.ProcessCallback | None = None,
        utility_tool_names: list[UtilityToolRef] | None = None,
        # 兼容旧拼写：``agent_configs`` / ``on_process`` 是本方法形参的旧名，
        # 历史调用点仍按旧名传。群聊路径只有这一个调用方（``_build_orchestrator_agent``，
        # 已统一为新名），但保留别名可以让签名探测类的旧调用不至于直接 TypeError。
        agent_configs: typing.Sequence[agent_runtime_module.AgentRuntime] | None = None,
        # 会话板：群聊共享的广播通道。当前 ``groupchat`` 包尚未落地，这里
        # 接受但**不要求**它存在——真正用到板子时由 ``_bind_chat_board`` 尽力挂载。
        board: typing.Any = None,
    ) -> agent_module.ReActAgent:
        """构建群聊编排者 agent。

        :param host: 编排宿主（``AgentOrchestrator``），用于取 LLM 配置、
            observer、根槽位名与运行时容器。
        :param agent_factory: agent 工厂；唯一知道「怎么把配置变成
            :class:`ReActAgent`」的一方，缺失即无法构建。
        :param root_agent_config: 根 agent 配置，提供系统提示词与能力约束。
        :param sub_agents: 参与群聊的 agent 配置（对等参与者）。
        :param on_progress: 进度回调；签名不固定，按多种形态依次尝试。
        :param utility_tool_names: ``(参与者名, 工具名)`` 列表；这些参与者会
            被额外登记为可点名对象，工具名则并入编排者的工具白名单。
        :param board: 群聊会话板；缺失时降级为「无板广播」。
        :return: 群聊编排者实例，``name`` 已按根槽位名确定。
        :raises RuntimeError: 工厂不可用或构建失败——见模块文档的失败策略。
        """
        if agent_factory is None:
            raise RuntimeError(
                f"{_LOG_PREFIX}groupchat orchestrator requires an AgentFactory; "
                "bootstrap did not provide one"
            )

        peers = list(sub_agents if sub_agents is not None else agent_configs or ())
        tool_refs = self._normalize_tool_refs(utility_tool_names)
        callback = on_progress if on_progress is not None else on_progress

        mode_decl = self._active_declaration(host)
        orchestrator_name = self._resolve_orchestrator_name(host, mode_decl, root_agent_config)
        slot = self._build_orchestrator_slot(
            orchestrator_name,
            peers=peers,
            tool_refs=tool_refs,
            mode_decl=mode_decl,
        )
        runtime_config = self._build_runtime_config(
            host,
            slot=slot,
            peers=peers,
            tool_refs=tool_refs,
            root_agent_config=root_agent_config,
            agent_factory=agent_factory,
            mode_decl=mode_decl,
        )
        unique_name = self._unique_runtime_name(host, orchestrator_name)

        await self._report(callback, "Creating groupchat orchestrator ...")
        # 插件据此裁剪本槽位可见的工具；不设置的话插件会当成「无限制」，
        # 群聊模式下等于把编排者能看到的工具全部放开。
        plugin_request_context.set_current_slot(slot)
        agent = await self._create_via_factory(
            agent_factory,
            runtime_config=runtime_config,
            unique_name=unique_name,
            observer=getattr(getattr(host, "app", None), "observer", None),
            on_progress=callback,
        )
        if agent is None:
            raise RuntimeError(
                f"{_LOG_PREFIX}groupchat orchestrator {unique_name!r} was not produced by the factory"
            )

        self._bind_chat_board(host, board)
        self._remember_runtime(host, unique_name, agent)
        await self._report(callback, "Groupchat orchestrator ready.", agent=unique_name)
        return agent

    # --- 名称 ---------------------------------------------------------------

    @staticmethod
    def _active_declaration(
        host: typing.Any,
    ) -> plugin_contracts.ModeDeclaration | None:
        """当前生效的模式声明；读不到返回 ``None``（后续走兜底）。

        注意 ``mode_registry.get(...)`` 直接返回 ``ModeDeclaration`` 本身，
        没有 ``.declaration`` 这一层包装（与 ``_interpret_mode`` 的注释一致）。
        """
        app = getattr(host, "app", None)
        declaration = getattr(app, "active_mode_declaration", None)
        if declaration is not None:
            return declaration
        registry = getattr(app, "mode_registry", None)
        getter = getattr(registry, "get", None)
        if not callable(getter):
            return None
        try:
            return getter(getattr(app, "app_mode", "") or "")
        except Exception:
            logger.debug("%smode declaration unavailable", _LOG_PREFIX, exc_info=True)
            return None

    @staticmethod
    def _resolve_orchestrator_name(
        host: typing.Any,
        mode_decl: plugin_contracts.ModeDeclaration | None,
        root_agent_config: typing.Any,
    ) -> str:
        """编排者的 agent 名。

        优先级：**模式声明的根槽位名 > 根配置名 > ``planner``**。

        根槽位名优先是因为它是 ``_interpret_mode`` 选根时用的键——编排者的名字
        必须与它一致，否则调用点拿到根 agent 后按名字登记时会对不上。
        """
        for candidate in (
            getattr(mode_decl, "root_agent", None) if mode_decl is not None else None,
            getattr(root_agent_config, "name", None),
        ):
            text = str(candidate or "").strip()
            if text:
                return text
        logger.debug(
            "%sno orchestrator name declared; falling back to %r",
            _LOG_PREFIX,
            _FALLBACK_ORCHESTRATOR_NAME,
        )
        return _FALLBACK_ORCHESTRATOR_NAME

    @staticmethod
    def _unique_runtime_name(host: typing.Any, base: str) -> str:
        """在宿主运行时容器里去重，避免后建的把先建的顶掉。

        重名的后果很隐蔽：整棵 agent 树里会凭空少一个节点，而链路看起来正常。
        """
        app = getattr(host, "app", None)
        runtimes = getattr(app, "agent_runtimes", None)
        if not isinstance(runtimes, dict) or base not in runtimes:
            return base
        suffix = 2
        while f"{base}-{suffix}" in runtimes:
            suffix += 1
        unique = f"{base}-{suffix}"
        logger.debug(
            "%sorchestrator name %r taken; using %r", _LOG_PREFIX, base, unique
        )
        return unique

    # --- 槽位与运行时配置 -----------------------------------------------------

    def _build_orchestrator_slot(
        self,
        orchestrator_name: str,
        *,
        peers: typing.Sequence[agent_runtime_module.AgentRuntime],
        tool_refs: typing.Sequence[UtilityToolRef],
        mode_decl: plugin_contracts.ModeDeclaration | None,
    ) -> plugin_contracts.AgentSlot:
        """构造编排者槽位声明。

        群聊里参与者**全部**对编排者可见，因此 ``subagent_visibility`` 直接列全
        ——可见性在这里表达的是「可以点名谁发言」，而不是权限隔离。
        """
        default_policies: dict[typing.Any, typing.Any] = {}
        if mode_decl is not None:
            default_policies = dict(getattr(mode_decl, "default_contribution_policy", {}) or {})
        return plugin_contracts.AgentSlot(
            name=orchestrator_name,
            agent_type=_ORCHESTRATOR_AGENT_TYPE,
            agent_role=agent_role_module.AgentRole.ORCHESTRATOR,
            subagent_visibility=tuple(self._peer_names(peers, tool_refs)),
            contribution_policies=default_policies,
        )

    def _build_runtime_config(
        self,
        host: typing.Any,
        *,
        slot: plugin_contracts.AgentSlot,
        peers: typing.Sequence[agent_runtime_module.AgentRuntime],
        tool_refs: typing.Sequence[UtilityToolRef],
        root_agent_config: typing.Any,
        agent_factory: typing.Any,
        mode_decl: plugin_contracts.ModeDeclaration | None,
    ) -> agent_runtime_module.AgentRuntime:
        """把槽位 + 根配置合成为一份 ``AgentRuntime``（工厂的唯一输入）。

        素材几乎全部来自根配置——编排者和根 agent 是同一个角色在两种拓扑下的
        化身，所以提示词/能力约束沿用根配置，只有「可见参与者」这一项是群聊独有的。
        """
        return agent_runtime_module.AgentRuntime(
            name=slot.name,
            description=str(getattr(root_agent_config, "description", "") or f"{slot.name} agent"),
            max_tokens=self._resolve_max_tokens(host, root_agent_config, agent_factory),
            system_prompt=str(getattr(root_agent_config, "system_prompt", "") or ""),
            llm_config=self._resolve_llm_config(host),
            mcp_servers=(
                list(getattr(root_agent_config, "mcp_servers", ()) or ())
                if slot.include_mcp_tools
                else []
            ),
            allowed_tools=self._resolve_allowed_tools(root_agent_config, tool_refs),
            skill_refs=(
                list(getattr(root_agent_config, "skill_refs", ()))
                if getattr(root_agent_config, "skill_refs", None) is not None
                else None
            ),
            strict_mcp_servers=bool(getattr(root_agent_config, "strict_mcp_servers", False)),
            load_agents_md=bool(getattr(root_agent_config, "load_agents_md", True)),
            allowed_agents=self._peer_names(peers, tool_refs),
            include_agent_tools=slot.include_agent_tools,
            agent_slot=slot,
            agent_role=agent_role_module.AgentRole.resolve(None, slot),
        )

    def _resolve_max_tokens(
        self,
        host: typing.Any,
        root_agent_config: typing.Any,
        agent_factory: typing.Any,
    ) -> int:
        """生成上限：根配置显式值 > 工厂配置 > 内置兜底。

        优先复用宿主上的 ``_resolve_root_max_tokens``——它已经处理了「配置写的是
        ``0`` 表示沿用」这类约定，本类不重复实现一套。
        """
        resolver = getattr(host, "_resolve_root_max_tokens", None)
        if callable(resolver):
            try:
                resolved = resolver(root_agent_config, agent_factory)
                if isinstance(resolved, int) and not isinstance(resolved, bool) and resolved >= 0:
                    return resolved
            except Exception:
                logger.debug("%sresolve max tokens failed; using fallback", _LOG_PREFIX, exc_info=True)
        fallback = getattr(root_agent_config, "max_tokens", None)
        if isinstance(fallback, int) and not isinstance(fallback, bool) and fallback >= 0:
            return fallback
        return 150_000

    def _resolve_llm_config(self, host: typing.Any) -> typing.Any:
        """取宿主的 LLM 配置。

        走宿主的 ``llm_config`` 转发属性而不是 ``host.app.llm_config``：两者必须
        永远相等（见 ``AgentOrchestrator.llm_config`` 的注释），同一个答案只留一个
        取法。

        转发属性可能读出 ``None``（宿主没配好模型），此时**不能**就此收手——
        ``None`` 会一路流进 ``AgentRuntime``，报错点被推迟到工厂内部的深层调用。
        所以判据是「取到非空值」，而不是「属性存在」。
        """
        candidates = (
            getattr(host, "llm_config", None),
            getattr(getattr(host, "app", None), "llm_config", None),
        )
        for config in candidates:
            if config is not None:
                return config
        raise RuntimeError(
            f"{_LOG_PREFIX}host exposes no llm_config; cannot build groupchat orchestrator"
        )

    def _resolve_allowed_tools(
        self,
        root_agent_config: typing.Any,
        tool_refs: typing.Sequence[UtilityToolRef],
    ) -> list[str] | None:
        """工具白名单：根配置的白名单与 ``utility_tool_names`` 的工具名取并集。

        根配置的白名单是 ``None``（不限制）时，仍要保留参与者工具这一项，否则
        调用点声明的 ``utility_tool_names`` 会被无声忽略。
        """
        configured = getattr(root_agent_config, "allowed_tools", None)
        if configured is None:
            allowed: list[str] | None = None
        else:
            allowed = list(configured)
        for _, tool_name in tool_refs:
            if not tool_name:
                continue
            if allowed is None:
                allowed = [tool_name]
            elif tool_name not in allowed:
                allowed.append(tool_name)
        return allowed

    @staticmethod
    def _normalize_tool_refs(
        utility_tool_names: typing.Iterable[typing.Any] | None,
    ) -> list[UtilityToolRef]:
        """把 ``utility_tool_names`` 归一成 ``(参与者名, 工具名)`` 二元组列表。

        容忍长度不为 2 的元素与任意可迭代元素：这份数据来自调用点约定而不是
        类型系统，脏值不该让群聊整体构建失败——丢掉这一项即可。
        """
        normalized: list[UtilityToolRef] = []
        for item in utility_tool_names or ():
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                logger.warning(
                    "%signored malformed utility_tool_names entry: %r", _LOG_PREFIX, item
                )
                continue
            agent_name, tool_name = str(item[0]).strip(), str(item[1]).strip()
            if agent_name and tool_name:
                normalized.append((agent_name, tool_name))
        return normalized

    @staticmethod
    def _peer_names(
        peers: typing.Sequence[agent_runtime_module.AgentRuntime],
        tool_refs: typing.Sequence[UtilityToolRef],
    ) -> list[str]:
        """可点名发言的参与者名单（去重且保序）。

        名单来源：参与者的配置名，加上 ``utility_tool_names`` 里点名的参与者——
        后者可能由调用点单独给出而不在 ``peers`` 中。
        """
        names: list[str] = [str(peer.name) for peer in peers if getattr(peer, "name", None)]
        names.extend(agent_name for agent_name, _ in tool_refs)
        return list(dict.fromkeys(names))

    # --- 构建与登记 -----------------------------------------------------------

    async def _create_via_factory(
        self,
        agent_factory: typing.Any,
        *,
        runtime_config: agent_runtime_module.AgentRuntime,
        unique_name: str,
        observer: typing.Any,
        on_progress: orchestration_contracts.ProcessCallback | None,
    ) -> agent_module.ReActAgent | None:
        """交给工厂创建编排者；按「信息量从多到少」探测签名。

        工厂的 ``create_react_agent`` 在不同实现上接受的参数并不一致，所以依次
        尝试：完整关键字 → 仅配置与改名 → 仅配置。``TypeError`` 视为签名不匹配
        继续下一种；其它异常视为真实故障，记录后放弃（避免把内部错误误判成签名
        问题而反复重试）。
        """
        attempts = (
            lambda: agent_factory.create_react_agent(
                sub_agent=runtime_config,
                override_name=unique_name,
                observer=observer,
                on_progress=on_progress,
            ),
            lambda: agent_factory.create_react_agent(
                sub_agent=runtime_config,
                override_name=unique_name,
            ),
            lambda: agent_factory.create_react_agent(sub_agent=runtime_config),
        )
        for attempt in attempts:
            try:
                agent = attempt()
                if inspect.isawaitable(agent):
                    agent = await agent
            except TypeError:
                logger.debug(
                    "%sfactory signature mismatch; retrying with fewer keywords",
                    _LOG_PREFIX,
                    exc_info=True,
                )
                continue
            except Exception:
                logger.exception("%sfactory failed to build orchestrator", _LOG_PREFIX)
                return None
            return agent if agent is not None else None
        return None

    def _bind_chat_board(self, host: typing.Any, board: typing.Any) -> None:
        """把会话板挂到宿主，供后续发言投递使用。

        尽力而为：``groupchat`` 包尚未落地，宿主上也可能没有这个属性（``__slots__``
        之类）。挂不上只记 debug——没有板子时群聊退化为「按顺序点名」，编排者本身
        仍然可用。
        """
        app = getattr(host, "app", None)
        if app is None:
            return
        target = board if board is not None else getattr(app, "_chat_board", None)
        if target is None:
            return
        for attribute in ("chat_board", "_chat_board"):
            try:
                setattr(app, attribute, target)
            except Exception:
                logger.debug(
                    "%scannot attach chat board as %r", _LOG_PREFIX, attribute, exc_info=True
                )
                continue
            logger.debug("%schat board attached as %r", _LOG_PREFIX, attribute)
            return

    @staticmethod
    def _remember_runtime(host: typing.Any, agent_name: str, agent: typing.Any) -> None:
        """把编排者登记到宿主运行时容器。

        群聊模式的参与者在装配期尚未实例化，这里只登记编排者；容器不是字典
        （或宿主不接受该属性）时静默跳过——登记不是本类的职责，调用点还有
        ``_register_created_agents`` 这条正路。
        """
        app = getattr(host, "app", None)
        runtimes = getattr(app, "agent_runtimes", None)
        if not isinstance(runtimes, dict):
            return
        try:
            runtimes[agent_name] = agent
        except Exception:
            logger.debug("%scannot register orchestrator runtime", _LOG_PREFIX, exc_info=True)

    # --- 进度 ---------------------------------------------------------------

    @staticmethod
    async def _report(
        on_progress: typing.Any,
        message: str,
        **payload: object,
    ) -> None:
        """向进度回调汇报一条消息。

        调用点只保证「给一个回调和一句话」，回调签名没有约定，因此按
        「纯文本 → 结构化字典 → 无参」依次尝试，命中即止。进度汇报不是主链路：
        回调报错只记日志，不能因为它把装配搞挂。
        """
        if on_progress is None:
            return
        if not callable(on_progress):
            logger.warning("%sprogress callback is not callable, ignored", _LOG_PREFIX)
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
                logger.exception("%sprogress report failed: %s", _LOG_PREFIX, message)
                return
            return
        logger.debug("%sprogress callback signature not matched: %s", _LOG_PREFIX, message)

    def __repr__(self) -> str:
        return "GroupChatAgentBuilder()"

    #: 兼容别名：调用点把方法名写成 ``builder`` 时也能命中 ``build``。
    builder = build
