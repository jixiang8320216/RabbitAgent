"""MCP 工具投影模式（tool projection mode）。

MCP 服务会暴露大量工具，把全部工具一次性塞进模型的工具列表会带来两个问题：
提示词体积膨胀、以及模型在过多相似工具间选择困难。因此工具如何「投影」给
agent 需要一种策略：

- ``EAGER``：启动时把已连接 MCP 服务的工具**全部**展开进 agent 的工具集。
  实现简单、调用链路短，适合工具数量可控的场景。
- ``GATEWAY``：只向 agent 暴露一个「网关工具」，由它在运行时按需检索并转发
  到具体 MCP 工具。适合工具数量很大的场景，代价是多一跳间接调用。

模式由 ``agent_orchestrator._resolve_mcp_tool_projection_mode`` 决定：启用
``mcp_tool_gateway`` 插件时走 ``GATEWAY``，否则 ``EAGER``。
"""

from __future__ import annotations

import enum
import typing

__all__ = ["MCPToolProjectionMode", "DEFAULT_PROJECTION_MODE", "resolve_projection_mode"]

#: ``_resolve_mcp_tool_projection_mode`` 中判定网关模式的插件名。
MCP_TOOL_GATEWAY_PLUGIN_NAME = "mcp_tool_gateway"


class MCPToolProjectionMode(str, enum.Enum):
    """MCP 工具投影到 agent 的方式。

    继承 ``str`` 是为了让模式值能直接参与字符串比较与序列化（配置落盘、
    日志打印、与插件名比较），而不必到处写 ``.value``。
    """

    EAGER = "eager"
    GATEWAY = "gateway"


DEFAULT_PROJECTION_MODE = MCPToolProjectionMode.EAGER


def resolve_projection_mode(
    active_plugin_names: typing.Iterable[str] | None,
    *,
    gateway_plugin_name: str = MCP_TOOL_GATEWAY_PLUGIN_NAME,
) -> MCPToolProjectionMode:
    """根据已启用插件名解析投影模式。

    :param active_plugin_names: 当前已启用的插件名集合。
    :param gateway_plugin_name: 网关插件名；命中即走 ``GATEWAY``。
    :return: 解析出的投影模式；无插件信息时返回默认模式 ``EAGER``。
    """
    names = set(active_plugin_names or ())
    if gateway_plugin_name in names:
        return MCPToolProjectionMode.GATEWAY
    return DEFAULT_PROJECTION_MODE
