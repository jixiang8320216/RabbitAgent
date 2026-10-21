"""domains.protocol 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.protocol.base import Protocol
from domains.protocol.a2a import A2AAgent, A2AServer, A2AClient, AgentNetwork, AgentRegistry, A2AMessage, MessageType, create_message, parse_message
from domains.protocol.anp import ANPDiscovery, ANPNetwork, ServiceInfo, register_service, discover_service


"""智能体通信协议模块

本模块提供三种主要的智能体通信协议：
- MCP (Model Context Protocol): 模型上下文协议
- A2A (Agent-to-Agent Protocol): 智能体间通信协议
- ANP (Agent Network Protocol): 智能体网络协议

简洁导入示例：
    >>> from rabbit_agents.protocols import MCPClient, MCPServer
    >>> from rabbit_agents.protocols import A2AServer, A2AClient, AgentNetwork
    >>> from rabbit_agents.protocols import ANPDiscovery, ANPNetwork

完整导入示例（向后兼容）：
    >>> from rabbit_agents.protocols.mcp import MCPClient, MCPServer
    >>> from rabbit_agents.protocols.a2a import A2AServer, A2AClient
    >>> from rabbit_agents.protocols.anp import ANPDiscovery, ANPNetwork
"""


try:
    from domains.protocol.mcp import (
        MCPClient,
        MCPServer,
        create_context,
        parse_context,
    )
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    # 提供占位符
    class MCPClient:
        def __init__(self, *args, **kwargs):
            raise ImportError("MCP requires fastmcp: pip install fastmcp")
    class MCPServer:
        def __init__(self, *args, **kwargs):
            raise ImportError("MCP requires fastmcp: pip install fastmcp")
    def create_context(*args, **kwargs):
        raise ImportError("MCP requires fastmcp: pip install fastmcp")
    def parse_context(*args, **kwargs):
        raise ImportError("MCP requires fastmcp: pip install fastmcp")
