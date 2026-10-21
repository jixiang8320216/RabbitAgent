"""domains.protocol.mcp 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "FASTMCP_AVAILABLE": "domains.protocol.mcp.client",
    "MCPClient": "domains.protocol.mcp.client",
    "MCPServer": "domains.protocol.mcp.server",
    "MCPServerBuilder": "domains.protocol.mcp.server",
    "MCP_CLIENT_AVAILABLE": "domains.protocol.mcp._types",
    "MCP_SERVER_AVAILABLE": "domains.protocol.mcp._types",
    "create_context": "domains.protocol.mcp.utils",
    "create_error_response": "domains.protocol.mcp.utils",
    "create_example_server": "domains.protocol.mcp.server",
    "create_success_response": "domains.protocol.mcp.utils",
    "parse_context": "domains.protocol.mcp.utils",}


def __getattr__(name):
    import importlib

    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
