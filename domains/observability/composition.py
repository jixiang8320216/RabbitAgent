import threading
import logging
import pydantic
import collections.abc
import collections.abc as abc

import domains.observability.telemetry_config as observability_configuration
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.observability.application.telemetry_policy_state as application_configuration
import domains.observability.application.observability_context as observability_context
import domains.observability.application.telemetry_runtime as telemetry_runtime
import domains.observability.domain.telemetry as telemetry
import domains.observability.infrastructure.network.network_zone_detector as network_zone_detector
import domains.observability.infrastructure.opentelemetry.event_sink as event_sink
import domains.observability.infrastructure.identity.process_owner as process_owner
_composition_lock = threading.Lock()
logger = logging.getLogger(__name__)

# 初始化遥测： 配置遥测并显式启动选定的平台适配器
def initialize_telemetry(
    entry_point:str,
    config_data: collections.abc.Mapping[str,pydantic.JsonValue]|None = None,
)->observability_configuration.TelemetryConfig:
    if entry_point not in {"cli","web"}:
        raise ValueError(f"Invalid entry_point {entry_point}")
    runtime_config =dict(config_data) if config_data is not None else execution_context.get_effective_runtime_config() # 读取运行时配置
    config= observability_configuration.TelemetryConfig.from_runtime_config(runtime_config) #构建遥测配置
    application_configuration.configure_telemetry(config.to_policy()) #配置遥测策略
    otel_config=config.to_opentelemetry()
    observability_context.set_entry_point(entry_point)

    with _composition_lock:
        if not config.enabled:
            observability_context.configure_process_owner(None)
            previous = telemetry_runtime.reset_sinks()
            _shutdown_sinks(previous)
            return config
        owner = _resolve_process_owner()
        observability_context.configure_process_owner(owner)
        zone=_dect_network_zone()
        observability_context.set_network_zone(zone)
        resolved_event_sink: telemetry.EventSink= telemetry_runtime.NullEventSink()
        resolved_metric_sink: telemetry.MetricSink =telemetry_runtime.NullMetricSink()

        try:
            resolved_event_sink = event_sink.OpenTelemetryEventSink(
                otel_config,
                entry_point=entry_point,
                process_owner=owner,
                network_zone=zone,
            )
        except Exception as e:
            logger.warning(e)

        previous= telemetry_runtime.replace_sinks(
            event_sink=resolved_event_sink,
            metric_sink=resolved_metric_sink,
        )

        _shutdown_sinks(previous)
        logger.info("[Telemetry] Initialized with entry point=%s", entry_point)
        return config

def _dect_network_zone():
    try:
        zone= network_zone_detector.detect_network_zone()
        return zone.value
    except Exception as exc:
        logger.warning(exc)
        return network_zone_detector.NetworkZone.UNKNOWN.value

def _resolve_process_owner() ->str|None:
    try:
        owner= process_owner.ProcessOwnerService().get_process_owner()
        return owner
    except Exception as exc:
        logger.warning(exc)
        return None


def _shutdown_sinks(sinks: tuple[telemetry.EventSink, telemetry.MetricSink]) -> None:
    for sink in sinks:
        try:
            sink.shutdown()
        except Exception as e:
            logger.warning(e)



















