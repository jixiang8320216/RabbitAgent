"""domains.observability 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "CANCEL_STATUS_VALUES": "domains.observability.domain.event_models",
    "CancelStatusEnum": "domains.observability.domain.event_models",
    "CompositeRabbitObserver": "domains.observability.application.observer_composition",
    "GlobFilesStructuredResult": "domains.observability.domain.tool_result_projection",
    "GrepContentStructuredResult": "domains.observability.domain.tool_result_projection",
    "InfraRuntimeObserver": "domains.observability.infrastructure.observers.runtime_observer_bridge",
    "NoOpObserver": "domains.observability.infrastructure.observers.noop_observer",
    "ObservabilityPolicy": "domains.observability.domain.policy",
    "OpenTelemetryEventSink": "domains.observability.infrastructure.opentelemetry.event_sink",
    "ReadStructuredResult": "domains.observability.domain.tool_result_projection",
    "RabbitObserver": "domains.observability.domain.rabbit_observer",
    "SLOW_CALL_THRESHOLD_MS": "domains.observability.application.guard",
    "StructuredToolResult": "domains.observability.domain.tool_result_projection",
    "StructuredToolResultModel": "domains.observability.domain.tool_result_projection",
    "TelemetryConfig": "domains.observability.telemetry_config",
    "TelemetrySeverity": "domains.observability.domain.telemetry",
    "emit_agent_finish": "domains.observability.application.emitter",
    "emit_agent_start": "domains.observability.application.emitter",
    "emit_query_end": "domains.observability.application.emitter",
    "emit_query_start": "domains.observability.application.emitter",
    "emit_tool_execute": "domains.observability.application.emitter",
    "end_query_tracking": "domains.observability.application.tracking",
    "generate_trace_id": "domains.observability.application.observability_context",
    "get_agent_invoke_count": "domains.observability.application.observability_context",
    "get_app_mode": "domains.observability.application.observability_context",
    "get_query_start_time": "domains.observability.application.observability_context",
    "get_session_type": "domains.observability.application.observability_context",
    "get_telemetry_config": "domains.observability.application.telemetry_policy_state",
    "increment_agent_invoke_count": "domains.observability.application.observability_context",
    "initialize_telemetry": "domains.observability.composition",
    "is_telemetry_enabled": "domains.observability.application.telemetry_policy_state",
    "reset_agent_invoke_count": "domains.observability.application.observability_context",
    "reset_query_stats": "domains.observability.application.observability_context",
    "set_app_mode": "domains.observability.application.observability_context",
    "set_query_start_time": "domains.observability.application.observability_context",
    "set_session_type": "domains.observability.application.observability_context",
    "set_trace_id": "domains.observability.application.observability_context",
    "start_query_tracking": "domains.observability.application.tracking",
    "telemetry": "domains.observability.application.guard",
    "telemetry_guard": "domains.observability.application.guard",}


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
