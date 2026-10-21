"""MCP 工具注册。

把「子 agent 声明要用哪些 MCP 服务」变成「工具集里真正可调用的工具」。

成熟度说明（决定了本模块为什么写得这么保守）：
``MCPManager`` 目前只稳定暴露**服务注册表**，工具级枚举接口没有定型。所以在
工具级清单拿不到时，这里登记的是**服务名**而不是工具名，并且记一条降级说明；
等服务侧支持工具级枚举后，无需改动调用方即可自动细化。

因此 :meth:`AgentMcpToolRegistrar.register` **绝不抛异常**：MCP 缺席只意味着
这个来源的工具不在装配结果里。
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
import typing

logger = logging.getLogger(__name__)

__all__ = ["McpRegistrationResult", "AgentMcpToolRegistrar"]


@dataclasses.dataclass(slots=True)
class McpRegistrationResult:
    """一次 MCP 注册的产物。

    :param registered_tool_names: 真正进了工具集的工具名。
    :param mcp_server_names: 本次纳入的 MCP 服务名（即使是服务级登记也算）。
    :param warnings: 降级说明；为空表示没有降级。
    """

    registered_tool_names: tuple[str, ...] = ()
    mcp_server_names: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.registered_tool_names) or bool(self.mcp_server_names)


class AgentMcpToolRegistrar:
    """把 MCP 服务暴露的工具登记进 ``Toolkit``。"""

    def __init__(
        self,
        mcp_manager: typing.Any,
        app_instance: typing.Any = None,
        logger_instance: typing.Optional[logging.Logger] = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.app_instance = app_instance
        self._logger = logger_instance or logger

    # -- 主入口 ---------------------------------------------------------
    async def register(
        self,
        toolkit: typing.Any,
        tool_registry: typing.Any,
        *,
        sub_agent: typing.Any = None,
        agent_name: str = "",
        configured_servers: typing.Any = None,
        enabled: bool = True,
        auto_discover: bool = True,
        allowed_tools_names: typing.Optional[typing.AbstractSet[str]] = None,
        on_progress: typing.Any = None,
    ) -> McpRegistrationResult:
        """登记 MCP 来源的工具。

        :param toolkit: 目标工具集（``Toolkit``）；``None`` 时直接返回空结果。
        :param tool_registry: 来源登记表（``ToolRegistryService``）；可为空。
        :param configured_servers: 声明里的服务名集合。
        :param enabled: 总开关；``False`` 直接返回空结果。
        :param auto_discover: 是否允许自动发现（``mcp_binding_mode != manual``）。
        :param allowed_tools_names: 工具白名单；``None`` 表示不限制。
        :param on_process: 进度回调，同步/异步都收。
        """
        if not enabled or toolkit is None:
            return McpRegistrationResult()

        server_names = self._server_names(configured_servers, auto_discover=auto_discover)
        warnings: list[str] = []
        registered: list[str] = []

        for server_name in server_names:
            await self._report(on_progress, server_name)
            try:
                tools = self._tools_of(server_name)
            except Exception as exc:  # noqa: BLE001 - 单个服务不可用不能拖垮装配
                self._logger.debug("mcp server %r unavailable: %s", server_name, exc)
                warnings.append(f"MCP 服务 {server_name!r} 不可用：{exc}")
                self._remember(tool_registry, server_name, server_name)
                continue

            if not tools:
                # 工具级枚举不可用：按服务名登记，等细化。
                self._remember(tool_registry, server_name, server_name)
                self._logger.debug(
                    "mcp server %r exposes no tool-level listing; registered by server name",
                    server_name,
                )
                continue

            for tool_name, tool_func in tools:
                if allowed_tools_names is not None and tool_name not in allowed_tools_names:
                    continue
                registration = toolkit.register_tool_function(
                    tool_func,
                    name=tool_name,
                    group=server_name,
                    namesake_strategy="skip",
                    source="mcp",
                )
                if registration is None:
                    continue
                registered.append(tool_name)
                self._remember(tool_registry, tool_name, server_name)

        return McpRegistrationResult(
            registered_tool_names=tuple(registered),
            mcp_server_names=server_names,
            warnings=tuple(warnings),
        )

    # -- 内部 -----------------------------------------------------------
    def _server_names(
        self,
        configured_servers: typing.Any,
        *,
        auto_discover: bool,
    ) -> tuple[str, ...]:
        """本次纳入的服务名：声明优先，未声明且允许自动发现时用注册表全量。"""
        declared = self._text_tuple(configured_servers)
        if declared:
            return declared
        if not auto_discover:
            return ()
        manager = self.mcp_manager
        if manager is None:
            return ()
        registry = getattr(manager, "mcp_registry", None)
        for getter_name in ("names", "server_names", "list_names"):
            getter = getattr(registry, getter_name, None)
            if callable(getter):
                try:
                    return self._text_tuple(getter())
                except Exception:  # noqa: BLE001
                    self._logger.debug("mcp registry %r failed", getter_name, exc_info=True)
        return ()

    def _tools_of(self, server_name: str) -> list[tuple[str, typing.Any]]:
        """取某个服务暴露的工具；取不到返回空列表（**不抛**）。

        按候选接口依次试探：``tools_of`` / ``get_tools`` / ``list_tools`` /
        ``tools``。返回值形态可能是映射（名 -> 可调用）或序列，两种都收。
        """
        manager = self.mcp_manager
        if manager is None:
            return []
        for attr in ("tools_of", "get_tools", "list_tools"):
            method = getattr(manager, attr, None)
            if not callable(method):
                continue
            try:
                produced = method(server_name)
            except TypeError:
                continue
            if inspect.isawaitable(produced):
                # 同步上下文里无法 await；交给调用方的异步口径处理，这里跳过。
                self._logger.debug("mcp manager %r is async; skipped", attr)
                continue
            return self._normalize_tools(produced)

        container = getattr(manager, "tools", None)
        if isinstance(container, typing.Mapping):  # type: ignore[arg-type]
            return self._normalize_tools(container)
        return []

    @staticmethod
    def _normalize_tools(produced: typing.Any) -> list[tuple[str, typing.Any]]:
        """把「映射 / 序列」形态的工具清单归一成 ``[(名字, 可调用)]``。"""
        result: list[tuple[str, typing.Any]] = []
        if isinstance(produced, typing.Mapping):  # type: ignore[arg-type]
            items: typing.Iterable[tuple[typing.Any, typing.Any]] = produced.items()  # type: ignore[assignment]
        elif isinstance(produced, (list, tuple, set)):
            items = []
            for item in produced:
                name = getattr(item, "name", None)
                if not isinstance(name, str):
                    name = str(item)
                items.append((name, item))
        else:
            return result
        for name, tool in items:
            if not isinstance(name, str) or not name:
                continue
            func = tool if callable(tool) else getattr(tool, "func", None) or getattr(tool, "call", None)
            if func is None:
                continue
            result.append((name, func))
        return result

    def _remember(self, tool_registry: typing.Any, tool_name: str, server_name: str) -> None:
        """往来源登记表里记一条 MCP 来源；登记表缺席时静默跳过。"""
        if tool_registry is None:
            return
        for method_name in ("register_mcp_tool", "register_tool"):
            method = getattr(tool_registry, method_name, None)
            if not callable(method):
                continue
            try:
                if method_name == "register_mcp_tool":
                    method(tool_name, server_name)
                else:
                    method(tool_name, "mcp", server_name)
            except Exception:  # noqa: BLE001
                self._logger.debug("tool registry %r failed", method_name, exc_info=True)
            return

    async def _report(self, on_progress: typing.Any, server_name: str) -> None:
        """进度回调；签名不固定，同步/异步都收，回调本身出错不影响装配。"""
        if on_progress is None:
            return
        for args, kwargs in (
            ((server_name,), {}),
            ((), {"server_name": server_name}),
            ((), {"name": server_name}),
            ((), {"message": f"mcp:{server_name}"}),
        ):
            try:
                produced = on_progress(*args, **kwargs)
            except TypeError:
                continue
            except Exception:  # noqa: BLE001
                return
            if inspect.isawaitable(produced):
                await produced
            return

    @staticmethod
    def _text_tuple(value: typing.Any) -> tuple[str, ...]:
        """把「None / 字符串 / 序列」归一成非空字符串元组。"""
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,) if value.strip() else ()
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
