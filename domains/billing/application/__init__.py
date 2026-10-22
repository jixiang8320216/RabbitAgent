"""domains.billing.application 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**（与工程内其它聚合包一致）。
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "BudgetUsageSink": "domains.billing.application.token_usage_bridge",
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
