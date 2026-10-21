import threading
import domains.observability.domain.policy as policy

_configuration_lock = threading.Lock()
_telemetry_policy = policy.ObservabilityPolicy()


def get_telemetry_config()-> policy.ObservabilityPolicy:
    with _configuration_lock:
        return _telemetry_policy

def is_telemetry_enabled()->bool:
    return get_telemetry_config().enabled


def configure_telemetry(new_policy: policy.ObservabilityPolicy)->None:
    """配置遥测策略。

    由 ``composition.initialize_telemetry`` 在装配时调用。
    **历史缺口**：此前只有读取函数，缺这个写入入口 ——
    初始化时配置根本没生效，策略永远是默认值。
    """
    global _telemetry_policy
    with _configuration_lock:
        _telemetry_policy = new_policy

__all__ = [
    "configure_telemetry",
    "get_telemetry_config",
    "is_telemetry_enabled",
]
