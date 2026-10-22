"""domains.agent.domain.ports 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**（与工程内其它聚合包一致）。

这里放的是领域层向外暴露的**端口**（``typing.Protocol``）：它们属于最内层，
被上层实现、被上层和同层消费。把端口放在这里，上层就不用为了「拿到一个类型」
而反过来被领域层依赖 —— 依赖方向始终是 应用层 -> 领域层。
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentExecutionPathResolver": "domains.agent.domain.ports.execution_store",
    "AgentExecutionStore": "domains.agent.domain.ports.execution_store",
}


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
