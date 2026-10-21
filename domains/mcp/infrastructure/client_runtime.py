"""MCP 客户端运行时。

``MCPManager`` 是整个应用里 MCP 能力的唯一入口：它持有配置的 MCP 服务清单、
授权服务（工具调用要经过 ``guradrails`` 审批）、elicitation 处理器（MCP 服务
反过来向用户提问时的回传通道），以及工具投影模式（决定 MCP 工具是全部展开
给 agent，还是只暴露一个网关工具）。

装配位置：``AgentBootstrapService._configure_mcp_runtime`` 构造它并挂到
``app.mcp_manager``，随后 ``AgentFactory`` 用同一个实例构造 agent——**所有
agent 共享一个 MCP 运行时**，因此这里必须是可复用、可并发读的容器。

当前阶段只补「构造 + 注册表」：连接建立、工具装载、网关投影属于后续步骤，
本文件不假设它们的实现；注册表里放的是**已注册的**服务（尚未连接）。

关于参数名 ``runtime_confgis``：这是调用点的既有拼写（``confgis``），本文件
按调用点原样接收，同时兼容正确拼写 ``runtime_configs`` 与历史写法
``server_configs``——本项目大量模块仍处于空壳状态，构造签名随时可能被改，
多收几个别名比让启动在拼写差异上崩掉更划算。
"""

from __future__ import annotations

import logging
import typing

import domains.mcp.domain.tool_projection as tool_projection

logger = logging.getLogger(__name__)

__all__ = ["MCPManager", "MCPServerRegistry"]

#: 服务配置项的候选类型；实际可能是 ``MCPServerConfig`` / ``MCPServerResource`` /
#: ``RuntimeMCPServerConfig`` 或裸 dict，因此统一按鸭子类型消费。
ServerConfig = typing.Any


class MCPServerRegistry:
    """已注册 MCP 服务的只读容器。

    ``list_all()`` 是外部唯一必需的接口（``AgentBootstrapService`` 用它判断
    「有没有配置 MCP 服务」，从而决定是否汇报进度）。其余方法是给日志、
    诊断与后续工具装载用的便利访问。
    """

    __slots__ = ("_servers",)

    def __init__(self, servers: typing.Iterable[ServerConfig] = ()) -> None:
        self._servers: tuple[ServerConfig, ...] = tuple(servers)

    def list_all(self) -> tuple[ServerConfig, ...]:
        """返回全部已注册服务（注册顺序，不去重）。"""
        return self._servers

    def get(self, name: str) -> ServerConfig | None:
        """按名字取服务；未找到返回 ``None``。"""
        for server in self._servers:
            if _server_name(server) == name:
                return server
        return None

    def names(self) -> tuple[str, ...]:
        """全部服务名；无名服务以空串占位，保证与 ``list_all()`` 一一对应。"""
        return tuple(_server_name(server) for server in self._servers)

    def __len__(self) -> int:
        return len(self._servers)

    def __iter__(self) -> typing.Iterator[ServerConfig]:
        return iter(self._servers)

    def __contains__(self, name: object) -> bool:
        return any(_server_name(server) == name for server in self._servers)

    def __repr__(self) -> str:
        return f"MCPServerRegistry({list(self.names())!r})"


class MCPManager:
    """MCP 运行时管理器。

    四个协作者全部由调用方注入，且**都可以缺省**：缺省时能力降级（无授权
    服务则不做审批、无 elicitation 处理器则无法回应服务端提问），但不阻断
    构造——MCP 配不起来不应该让应用起不来。

    :param runtime_confgis: MCP 服务配置序列（调用点既有拼写，优先读取）。
    :param authorization_service: 授权服务；``guradrails`` 的
        ``AuthorizationService``，用于工具调用前的审批。
    :param elicitation_handler: elicitation 处理器；MCP 服务请求用户输入时
        由它把问题递给 UI。
    :param tool_projection_mode: 工具投影模式；非法值回落到
        ``MCPToolProjectionMode.EAGER``。
    :param runtime_configs: ``runtime_confgis`` 的正确拼写别名。
    :param server_configs: 历史别名。
    """

    def __init__(
        self,
        *,
        runtime_configs: typing.Sequence[ServerConfig] | None = None,
        authorization_service: typing.Any = None,
        tool_projection_mode: tool_projection.MCPToolProjectionMode | str | None = None,
        elicitation_handler: typing.Any = None,
        **extra: typing.Any,
    ) -> None:
        if extra:
            logger.warning(
                "[MCP] MCPManager 收到未识别的构造参数，已忽略: %s",
                sorted(extra),
            )
        self._authorization_service = authorization_service
        self._elicitation_handler = elicitation_handler
        self._tool_projection_mode = _coerce_projection_mode(tool_projection_mode)
        self._mcp_registry = MCPServerRegistry(
        )
        if authorization_service is None:
            logger.warning("[MCP] MCPManager 未装配 authorization_service；MCP 调用将不经过审批")
        if elicitation_handler is None:
            logger.debug("[MCP] MCPManager 未装配 elicitation_handler；服务端提问将无法回应")

    # --- 只读访问 ---------------------------------------------------------

    @property
    def mcp_registry(self) -> MCPServerRegistry:
        """已注册服务注册表；``list_all()`` 返回配置序列。"""
        return self._mcp_registry

    @property
    def authorization_service(self) -> typing.Any:
        """授权服务；未装配时为 ``None``。"""
        return self._authorization_service

    @property
    def elicitation_handler(self) -> typing.Any:
        """elicitation 处理器；未装配时为 ``None``。"""
        return self._elicitation_handler

    @property
    def tool_projection_mode(self) -> tool_projection.MCPToolProjectionMode:
        """工具投影模式。"""
        return self._tool_projection_mode

    @tool_projection_mode.setter
    def tool_projection_mode(
        self,
        value: tool_projection.MCPToolProjectionMode | str | None,
    ) -> None:
        self._tool_projection_mode = _coerce_projection_mode(value)

    @property
    def mcp_tool_projection_mode(self) -> tool_projection.MCPToolProjectionMode:
        """``tool_projection_mode`` 的别名（部分调用点用这个名字回写）。"""
        return self._tool_projection_mode

    @mcp_tool_projection_mode.setter
    def mcp_tool_projection_mode(
        self,
        value: tool_projection.MCPToolProjectionMode | str | None,
    ) -> None:
        self._tool_projection_mode = _coerce_projection_mode(value)

    @property
    def server_configs(self) -> tuple[ServerConfig, ...]:
        """已注册服务配置的只读视图，等价于 ``mcp_registry.list_all()``。"""
        return self._mcp_registry.list_all()

    def __len__(self) -> int:
        return len(self._mcp_registry)

    def __repr__(self) -> str:
        return (
            "MCPManager("
            f"servers={len(self._mcp_registry)}, "
            f"projection={self._tool_projection_mode.value}, "
            f"authorization={type(self._authorization_service).__name__}, "
            f"elicitation={type(self._elicitation_handler).__name__})"
        )


# --- 构造辅助 -------------------------------------------------------------


def _first_config_source(*sources: typing.Any) -> typing.Any:
    """按「调用点拼写 → 正确拼写 → 历史别名」取第一个非空配置来源。"""
    for source in sources:
        if source is None:
            continue
        if isinstance(source, typing.Mapping) and not source:
            continue
        if isinstance(source, typing.Sequence) and not isinstance(source, (str, bytes)):
            if len(source) == 0:
                continue
        return source
    return None


def _iter_configs(raw: typing.Any) -> list[ServerConfig]:
    """把配置来源摊平成列表。

    单个 dict 视为一条配置；``None`` / 空容器返回 ``[]``；不可迭代对象只记
    日志——调用点误传宿主或配置管理器时不该让构造崩掉。
    """
    if raw is None:
        return []
    if isinstance(raw, typing.Mapping):
        return [raw]
    if isinstance(raw, (str, bytes)):
        logger.warning("[MCP] 忽略非法的 MCP 服务配置（字符串）: %r", raw)
        return []
    try:
        iterator = iter(raw)
    except TypeError:
        logger.warning("[MCP] MCP 服务配置不可迭代，已忽略: %r", type(raw).__name__)
        return []
    return [item for item in iterator if item is not None]


def _server_name(config: ServerConfig) -> str:
    """取服务名；取不到返回空串（由调用方决定是否合成占位名）。"""
    for attribute in ("name", "server_name", "id"):
        value: typing.Any = None
        if isinstance(config, typing.Mapping):
            value = config.get(attribute)
        else:
            value = getattr(config, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_enabled(config: ServerConfig) -> bool:
    """判断服务是否启用。

    三种写法都认（``disabled`` / ``enabled`` / ``active``），都没有时按启用
    处理——缺省可用比缺省禁用更安全（禁用会让用户困惑于"配了却没生效"）。
    """
    for attribute, inverted in (("disabled", True), ("enabled", False), ("active", False)):
        value: typing.Any = None
        if isinstance(config, typing.Mapping):
            value = config.get(attribute)
        else:
            value = getattr(config, attribute, None)
        if isinstance(value, bool):
            return (not value) if inverted else value
    return True


def _register_servers(raw: typing.Any) -> tuple[ServerConfig, ...]:
    """把配置来源整理成注册表内容：跳过停用项、按名字去重。

    配置**原样保存不做归一化**：``MCPServerConfig`` 目前还没有字段定义，
    在这里做 ``model_validate`` 会把内容全部丢掉，等于把配置清空。
    """
    registered: list[ServerConfig] = []
    seen: set[str] = set()
    for config in _iter_configs(raw):
        if not _is_enabled(config):
            logger.info("[MCP] 跳过已停用的 MCP 服务: %r", _server_name(config))
            continue
        name = _server_name(config)
        if name and name in seen:
            logger.warning("[MCP] 重复的 MCP 服务名，已忽略后出现的那条: %r", name)
            continue
        if name:
            seen.add(name)
        registered.append(config)
    return tuple(registered)


def _coerce_projection_mode(
    value: tool_projection.MCPToolProjectionMode | str | None,
) -> tool_projection.MCPToolProjectionMode:
    """把投影模式归一到枚举；``None`` 或非法值回落到默认模式 ``EAGER``。"""
    if value is None:
        return tool_projection.DEFAULT_PROJECTION_MODE
    if isinstance(value, tool_projection.MCPToolProjectionMode):
        return value
    try:
        return tool_projection.MCPToolProjectionMode(str(value).strip().lower())
    except ValueError:
        logger.warning(
            "[MCP] 未知的工具投影模式 %r，回落到 %r",
            value,
            tool_projection.DEFAULT_PROJECTION_MODE.value,
        )
        return tool_projection.DEFAULT_PROJECTION_MODE
