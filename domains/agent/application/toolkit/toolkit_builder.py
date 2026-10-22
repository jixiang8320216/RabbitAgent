from __future__ import annotations

import inspect
import logging
import typing

import domains.agent.infrastructure.management_tools as management_tools
import domains.agent.application.loader.expert_call_service as expert_call_service
import domains.skill.service.skill_registry as skill_registry_module
import domains.mcp.infrastructure.client_runtime as client_runtime
import domains.tool.core.application.toolkit as toolkit_module
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.application.toolkit.toolkit_assembly as toolkit_assembly
import domains.agent.domain.entities.agent_role as agent_role_module
import domains.tool.core.application.registration as registration_module
import domains.agent.application.toolkit.mcp_tool_registration as agent_mcp_tool_registration
import collections.abc
import collections.abc as abc
import domains.tool.lsp as lsp

logger = logging.getLogger(__name__)

AgentToolkitProgressCallback=collections.abc.Callable[[str],collections.abc.Awaitable[None]]

class AgentToolkitApplicationPort(
    expert_call_service.ExpertCallApplicationPort,
    typing.Protocol,
):
    user_project_home: str


class AgentToolkitBuilder:
    """agent 工具集构造器。

    把「给 agent 装配工具所需的依赖」一次性收齐并持有：

    - ``mcp_manager``：MCP 运行时，外部工具（MCP server 暴露的工具）来源。
    - ``app_instance``：宿主应用（``AgentToolkitApplicationPort``）。
    - ``skill_registry`` / ``skill_registry_refresher``：技能注册表及其刷新器。

    装配位置：``AgentFactory.__init__``。依赖全部可选——缺哪一项都只是对应的
    工具来源缺席，不应让构造器本身构造失败。
    """

    def __init__(
        self,
        *,
        mcp_manager: client_runtime.MCPManager,
        app_instance: AgentToolkitApplicationPort|None=None,
        skill_registry: typing.Optional[skill_registry_module.SkillRegistry] = None,
        skill_registry_refresher: typing.Optional[
            skill_registry_module.SkillRegistryRefresher
        ] = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.app_instance = app_instance
        self.skill_registry = skill_registry
        self.skill_registry_refresher = skill_registry_refresher

    @staticmethod
    def _normalize_allowed_tool_names(
        allowed_tools: typing.Any,
    ) -> typing.Optional[frozenset[str]]:
        """把声明里的工具白名单归一成 ``frozenset``；未限定返回 ``None``。

        ``None`` 是「不限制」的意思，不能和「空集合（一个工具都不允许）」混为一谈：
        装配侧到处是 ``if allowed_tool_names is None`` 的判断，返回空集会让
        agent 一个工具都拿不到，且不报错。
        """
        names = _text_tuple(allowed_tools)
        return frozenset(names) if names else None

    @staticmethod
    def _tool_name_allowed(
        tool_name: str,
        allowed_tool_names: typing.Optional[typing.AbstractSet[str]],
    ) -> bool:
        """工具是否命中白名单；白名单为空集合同义于「全不允许」。"""
        if allowed_tool_names is None:
            return True
        return tool_name in allowed_tool_names

    async def _register_system_tool_selectively(
        self,
        toolkit: typing.Any,
        mcp_registered_tools: typing.AbstractSet[str],
        local_tool_registry_service: typing.Any,
        sub_agent: typing.Any,
    ) -> None:
        """补登记内置 / 系统工具，**跳过 MCP 已经占住的名字**。

        同名冲突就是本方法存在的全部理由：MCP 与内置工具都可能提供
        ``read_file`` 这类通用名。若让内置工具覆盖掉 MCP 已注册的实现，
        「同一个工具行为突然变了」将无从查起；所以这里一律让先到者生效。

        内置工具注册表目前尚未落地（``_builtin_names`` 会记一条降级说明），
        因此常态是找不到任何可登记的工具——那时直接返回，不产生副作用。
        """
        warnings: list[str] = []
        names = self._builtin_names(include_builtin_tools=True, warnings=warnings)
        for message in warnings:
            logger.debug("builtin tools: %s", message)
        if not names:
            return

        allowed = self._normalize_allowed_tool_names(getattr(sub_agent, "allowed_tools", None))
        provider = self._builtin_provider()
        if provider is None:
            return

        for name in names:
            if name in mcp_registered_tools:
                continue
            if allowed is not None and name not in allowed:
                continue
            func = self._lookup_builtin(provider, name)
            if func is None:
                continue
            registration = toolkit.register_tool_function(
                func,
                name=name,
                namesake_strategy="skip",
                source="builtin",
            )
            if registration is None:
                continue
            self._remember(local_tool_registry_service, name, "builtin")

    def _builtin_provider(self) -> typing.Any:
        """内置工具的来源（宿主暴露的映射 / 对象）；没有就返回 ``None``。"""
        app_instance = self.app_instance
        if app_instance is None:
            return None
        for attribute in ("builtin_tools", "system_tools", "tool_definitions"):
            provider = getattr(app_instance, attribute, None)
            if provider:
                return provider
        return None

    @staticmethod
    def _lookup_builtin(provider: typing.Any, name: str) -> typing.Any:
        """从内置来源里按名字取可调用对象；取不到返回 ``None``。"""
        if isinstance(provider, typing.Mapping):  # type: ignore[arg-type]
            candidate = provider.get(name)
        else:
            getter = getattr(provider, "get", None)
            candidate = getter(name) if callable(getter) else getattr(provider, name, None)
        if callable(candidate):
            return candidate
        # 工具对象形态：真正的函数挂在 func / call / runnable 上。
        for attribute in ("func", "call", "runnable", "fn"):
            nested = getattr(candidate, attribute, None)
            if callable(nested):
                return nested
        return None

    @staticmethod
    def _remember(tool_registry: typing.Any, tool_name: str, source: str) -> None:
        """往来源登记表里记一条；登记表缺席或接口不符时静默跳过。"""
        if tool_registry is None:
            return
        for method_name in ("register_builtin_tool", "register_tool"):
            method = getattr(tool_registry, method_name, None)
            if not callable(method):
                continue
            try:
                if method_name == "register_builtin_tool":
                    method(tool_name)
                else:
                    method(tool_name, source)
            except Exception:  # noqa: BLE001 - 登记表不是主流程
                logger.debug("tool registry %r failed", method_name, exc_info=True)
            return

    async def prepare_toolkit_assembly(self,
        sub_agent: agent_runtime_module.AgentRuntime,
        on_progress: AgentToolkitProgressCallback|None=None,
        include_builtin_tools:bool=True,
        include_mcp_tools: bool=True,
        **extra: typing.Any,
    )-> toolkit_assembly.AgentToolkitAssembly:
        """把一个子 agent 声明解析成工具集装配结果。

        流程：开关归一 → 逐来源收集工具名 → 按声明过滤 → 组装工具集 →
        生成来源解析器。

        工具来源目前成熟度不一，因此每一步都可能「拿不到」：

        - **内置工具**：注册表尚未落地，缺席时只记一条降级说明；
        - **MCP 工具**：``MCPManager`` 目前只暴露服务注册表，所以这里登记的是
          **服务名**；等它支持工具级枚举后无需改动本方法即可自动细化；
        - **技能工具**：``SkillRegistry`` 为空模块，同样降级；
        - **委派 agent**：直接取子 agent 声明里的 agent 引用。

        ``sub_agent`` 上的绑定模式（``*_binding_mmode``）为 ``manual`` 时，
        对应的声明列表（``allowed_tools`` / ``mcp_servers`` / ``skill_refs``）
        视为白名单；否则视为未限定。

        :param sub_agent: 子 agent 配置（``AgentRuntime``）。
        :param on_process: 进度回调；签名不固定，同步/异步都收。
        :param include_builtin_tools: 是否纳入内置工具。
        :param include_mcp_tools: 是否纳入 MCP 工具。
        :param extra: 兼容别名，例如调用点历史拼写 ``include_mcp_tool``；
            未识别的关键字直接忽略。
        :return: 装配结果；**不会因为任何来源缺席而抛异常**。
        """
        toolkit = toolkit_module.Toolkit()
        local_tool_registry_service= registration_module.ToolRegistryService(app_instance=self.app_instance)
        allowed_tool_names = self._normalize_allowed_tool_names(sub_agent.allowed_tools)
        mcp_result = await agent_mcp_tool_registration.AgentMcpToolRegistrar(
            self.mcp_manager,
            self.app_instance,
            logger,
        ).register(
            toolkit,
            local_tool_registry_service,
            sub_agent=sub_agent,
            agent_name= sub_agent.name,
            configured_servers= sub_agent.mcp_servers,
            enabled= include_mcp_tools,
            auto_discover=(
                sub_agent.mcp_binding_mode!="manual"
                and not sub_agent.resource_bindings_resolved
            ),
            allowed_tools_names=allowed_tool_names,
            on_progress=on_progress,
        )
        mcp_registered_tools = set(mcp_result.registered_tool_names)
        if include_builtin_tools:
            pre_system_tool_count=len(toolkit.tools)
            await self._register_system_tool_selectively(
                toolkit,
                mcp_registered_tools,
                local_tool_registry_service,
                sub_agent,
            )
            post_system_tool_count=len(toolkit.tools)
            logger.info("tookit now has %s tools (added %s system tools)",post_system_tool_count, post_system_tool_count-pre_system_tool_count)
        agent_role = agent_role_module.AgentRole.resolve(sub_agent.agent_role, sub_agent.agent_slot)

        if(
            self.app_instance is not None
            and sub_agent.include_agent_tools
            and agent_role is not agent_role_module.AgentRole.SUB_AGENT
        ):
            exper_service = expert_call_service.ExpertCallService(self.app_instance,sub_agent)
            toolkit.register_tool_function(exper_service.call_subagent,
            namesake_strategy="skip",
            is_agent=True,
            )
            local_tool_registry_service.register_subagent_tool(
                "call_subagent",
            )
            for management_tool in (
                management_tools.check_agent_status,
                management_tools.await_agent_result,
                management_tools.cancel_agent,
            ):
                toolkit.register_tool_function(
                    management_tool,
                    namesake_strategy="skip",
                )
        lsp_tool_names={
            "lsp_code_completion",
            "lsp_goto_definition",
            "lsp_hover_info",
            "lsp_get_diagnostics",
            "lsp_find_references",
            "lsp_rename",
        }

        if (
            self.app_instance is not None
            and self.app_instance.lsp_tools is not None
            and (
                allowed_tool_names is None
                or any(self._tool_name_allowed(tool_name,allowed_tool_names) for tool_name in lsp_tool_names)
            )
        ):
            try:
                adapter = lsp.LSPAgentTools(self.app_instance.lsp_tools)
                adapter.register_with_toolkit(toolkit)
                if allowed_tool_names is not None:
                    for tool_name in lsp_tool_names:
                        if not self._tool_name_allowed(tool_name,allowed_tool_names):
                            toolkit.tools.pop(tool_name,None)
            except Exception as e:
                logger.exception(e)
        final_tool_count = len(toolkit.tools)
        if allowed_tool_names is None:
            toolkit.expost_all_registered_tools=True

        return toolkit_assembly.AgentToolkitAssembly(
            toolkit=toolkit,
            tool_classification_resolver= local_tool_registry_service,
        )

    def _skill_names(
        self,
        sub_agent: typing.Any,
        *,
        warnings: list[str],
    ) -> tuple[str, ...]:
        """本次纳入的技能名；注册表缺席时退回声明里的技能引用。"""
        declared = _text_tuple(getattr(sub_agent, "skill_refs", None))
        registry = self.skill_registry
        known: tuple[str, ...] = ()
        if registry is not None:
            for getter in ("names", "skill_names", "list_names"):
                method = getattr(registry, getter, None)
                if callable(method):
                    try:
                        known = _text_tuple(method())
                    except Exception:  # noqa: BLE001
                        logger.debug("skill registry %r failed", getter, exc_info=True)
                    else:
                        break
            else:
                known = _text_tuple(
                    getattr(registry, "skills", None)
                    or getattr(registry, "_skills", None)
                )
        elif declared:
            warnings.append("skill_registry 未装配；技能工具缺席")

        if declared and _is_manual(getattr(sub_agent, "skill_binding_mmode", "")):
            return tuple(name for name in known if name in set(declared)) or declared
        return known or declared

    def _builtin_names(
        self,
        *,
        include_builtin_tools: bool,
        warnings: list[str],
    ) -> tuple[str, ...]:
        """内置工具名；内置注册表尚未落地时为空并记一条降级说明。"""
        if not include_builtin_tools:
            return ()
        warnings.append("内置工具注册表尚未装配；内置工具缺席")
        return ()

    def _build_toolkit(
        self,
        agent_name: str,
        *,
        warnings: list[str],
    ) -> typing.Any | None:
        """构造工具集对象；``Toolkit`` 尚未实现时返回 ``None``。"""
        factory = getattr(toolkit_module, "Toolkit", None)
        if factory is None:
            warnings.append(
                "tool.core.application.toolkit.Toolkit 尚未实现；toolkit 为 None"
            )
            return None
        candidates = {"name": agent_name, "agent_name": agent_name, "tools": ()}
        try:
            parameters = inspect.signature(factory).parameters
        except (TypeError, ValueError):
            parameters = None
        if parameters is None:
            kwargs: dict[str, typing.Any] = {}
        elif any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            kwargs = dict(candidates)
        else:
            kwargs = {k: v for k, v in candidates.items() if k in parameters}
        try:
            return factory(**kwargs)
        except Exception:  # noqa: BLE001
            logger.warning("cannot construct Toolkit for %r", agent_name, exc_info=True)
            warnings.append(f"Toolkit 构造失败：{agent_name!r}")
            return None

    @staticmethod
    async def _report(
        on_progress: AgentToolkitProgressCallback | None,
        message: str,
    ) -> None:
        """进度上报；回调缺失或抛异常都不影响装配。"""
        if on_progress is None:
            return
        try:
            result = on_progress(message)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001
            logger.debug("toolkit progress callback failed", exc_info=True)

    async def prepare_toolkit(
        self,
        sub_agent: agent_runtime_module.AgentRuntime,
        on_progress: AgentToolkitProgressCallback | None = None,
        include_builtin_tools: bool = True,
        include_mcp_tools: bool = True,
        )-> toolkit_module.Toolkit:
        assembly=await self.prepare_toolkit_assembly(
            sub_agent=sub_agent,
            on_progress=on_progress,
            include_builtin_tools=include_builtin_tools,
            include_mcp_tools=include_mcp_tools,
        )
        return assembly.toolkit


# -- 模块级辅助 -----------------------------------------------------------


def _coerce_switch(
    extra: dict[str, typing.Any],
    default: bool,
    *names: str,
) -> bool:
    """从 ``**extra`` 里挑一个存在的开关值；都没有就用形参默认值。

    调用点对同一个开关的命名不一致（``include_mcp_tool`` / ``include_mcp_tools``），
    这里按给定顺序取第一个**显式传入**的。
    """
    for name in names:
        if name in extra and extra[name] is not None:
            return bool(extra[name])
    return bool(default)


def _text_tuple(value: typing.Any) -> tuple[str, ...]:
    """把「None / 字符串 / 序列」归一成非空字符串元组。"""
    if value is None or isinstance(value, (str, bytes)):
        if isinstance(value, str) and value:
            return (value,)
        return ()
    try:
        items = list(value)
    except TypeError:
        return ()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _is_manual(binding_mode: typing.Any) -> bool:
    """绑定模式是否为「手工白名单」。未知取值按未限定处理。"""
    return str(binding_mode or "").strip().lower() == "manual"


def _agent_name(sub_agent: typing.Any) -> str:
    """子 agent 显示名；取不到给个占位，避免下游格式化炸掉。"""
    for attribute in ("name", "agent_name", "expert_id", "agent_id"):
        value = getattr(sub_agent, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _agent_tool_names(sub_agent: typing.Any) -> tuple[str, ...]:
    """可委派的 agent 名（``alowwed_agents`` 是既有拼写，一并兼容）。"""
    return _text_tuple(
        getattr(sub_agent, "allowed_agents", None)
        or getattr(sub_agent, "alowwed_agents", None)
        or getattr(sub_agent, "allow_agent_ids", None)
    )


def _allowed_tool_names(sub_agent: typing.Any) -> frozenset[str] | None:
    """工具白名单；手工绑定才有，未限定返回 ``None``。"""
    declared = _text_tuple(getattr(sub_agent, "allowed_tools", None))
    if not declared:
        return None
    if not _is_manual(getattr(sub_agent, "tool_binding_mmode", "")):
        return None
    return frozenset(declared)


def _build_classifications(
    *,
    builtin_names: tuple[str, ...],
    mcp_server_names: tuple[str, ...],
    skill_names: tuple[str, ...],
    agent_names: tuple[str, ...],
) -> list[toolkit_assembly.ToolClassification]:
    """把各来源的名字摊平成归类表；同名以先出现的来源为准。"""
    classifications: list[toolkit_assembly.ToolClassification] = []
    seen: set[str] = set()
    for source, names in (
        (toolkit_assembly.ToolSource.BUILTIN, builtin_names),
        (toolkit_assembly.ToolSource.MCP, mcp_server_names),
        (toolkit_assembly.ToolSource.SKILL, skill_names),
        (toolkit_assembly.ToolSource.AGENT, agent_names),
    ):
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            classifications.append(
                toolkit_assembly.ToolClassification(
                    name=name,
                    source=source,
                    origin=name if source is toolkit_assembly.ToolSource.MCP else "",
                )
            )
    return classifications

async def _register_system_tool_selectively(
    self,
    toolkit: toolkit_module.Toolkit,
    mcp_tools:set[str],
    tool_registry_service: registration_module.ToolRegistryService,
    sub_agent:agent_runtime_module.AgentRuntime,
)->None:
    """"""





