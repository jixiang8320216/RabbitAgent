"""domains.context.domain 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**（与工程内其它聚合包一致）。

这里放的是**纯领域逻辑**：不依赖框架、不依赖第三方 SDK、不 import 同域之外的
业务模块。历史压缩的策略计算就属于这一类——它需要能在完全没有 LLM 的环境里
被单测跑通。
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "COMPRESSION_MARK": "domains.context.domain.history_compression_policy",
    "CompressionPlan": "domains.context.domain.history_compression_policy",
    "CompressionReport": "domains.context.domain.history_compression_policy",
    "TOOL_BLOCK_TYPES": "domains.context.domain.history_compression_policy",
    "apply_compression": "domains.context.domain.history_compression_policy",
    "estimate_tokens": "domains.context.domain.history_compression_policy",
    "extractive_summary": "domains.context.domain.history_compression_policy",
    "has_tool_block": "domains.context.domain.history_compression_policy",
    "message_text": "domains.context.domain.history_compression_policy",
    "messages_tokens": "domains.context.domain.history_compression_policy",
    "select_compression_window": "domains.context.domain.history_compression_policy",
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
