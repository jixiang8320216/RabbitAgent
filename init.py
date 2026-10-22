"""RabbitAgentDDD 顶层兼容垫层。

DDD 重构后，顶层包已拆分为 domains/（业务域）+ infrastructure/（共享技术设施）
+ interfaces/（外部适配）。本文件保留旧版 ``init.py`` 的公开导出名，
使 ``from init import SimpleAgent, Message, ...`` 这类历史用法继续可用。

采用 PEP 562 模块级 __getattr__ 做惰性导出：
只有真正访问某个名字时才导入对应模块，避免在包初始化期间拉起整条
子模块链（含 pydantic 模型定义）而与反向依赖本模块的代码构成循环导入。

真正的包入口是根目录的 ``__init__.py``；本文件仅作向后兼容之用。
"""

# 配置第三方库的日志级别，减少噪音
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("qdrant_client").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)

from version import __version__, __author__, __email__, __description__

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    # 核心组件
    "LLM": "domains.llm",
    "Config": "domains.configuration.infrastructure.logging_bootstrap",
    "Message": "domains.agent.domain.value_objects.message",
    "AgentsException": "shared.exceptions",
    # Agent 实现
    "ReActAgent": "domains.agent.domain.react_agent",
    # 工具系统
    "ToolRegistry": "domains.tool.infrastructure.registry",
    "global_registry": "domains.tool.infrastructure.registry",
    "SearchTool": "domains.tool.builtin.search_tool",
    "search": "domains.tool.builtin.search_tool",
    "CalculatorTool": "domains.tool.builtin.calculator",
    "calculate": "domains.tool.builtin.calculator",
    "ToolChain": "domains.tool.infrastructure.chain",
    "ToolChainManager": "domains.tool.infrastructure.chain",
    "AsyncToolExecutor": "domains.tool.infrastructure.async_executor",
}


def __getattr__(name):
    import importlib

    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    # 版本信息
    "__version__",
    "__author__",
    "__email__",
    "__description__",

    # 核心组件
    "LLM",
    "Config",
    "Message",
    "AgentsException",

    # Agent范式
    "ReActAgent",

    # 工具系统
    "ToolRegistry",
    "global_registry",
    "SearchTool",
    "search",
    "CalculatorTool",
    "calculate",
    "ToolChain",
    "ToolChainManager",
    "AsyncToolExecutor",
]
