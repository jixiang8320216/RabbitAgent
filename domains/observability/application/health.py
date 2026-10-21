"""遥测健康监控。

收集**遥测链路自身**的健康信号：队列水位、端点可达性、采集异常。

为什么单独一层：这些是「观测系统的观测」。业务指标走 sink 上报，
而链路健康只能就地记录 —— 端点都不可达了，还指望它上报自己出问题吗。
因此健康状态保留在进程内，由 ``/health`` 之类的接口直接读取。

**历史缺口**：本模块此前是空文件，``event_sink`` 调用
``get_health_monitor().record_queue_level(...)`` 与
``get_health_module().record(...)`` 时会抛 AttributeError ——
而且恰好在异常处理路径上抛，把真正的原始异常掩盖掉。
"""

from __future__ import annotations

import threading
import typing

import domains.observability.domain.health as health_domain

# 队列水位达到该比例即视为「接近容量上限」
_QUEUE_WARN_RATIO = 0.8


class HealthMonitor:
    """进程内遥测健康状态收集器。

    线程安全：队列水位由导出线程更新，健康查询可能来自任意线程。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 队列名 -> (当前水位, 容量)
        self._queue_levels: typing.Dict[str, typing.Tuple[int, int]] = {}
        # 告警类别 -> 发生次数
        self._alerts: typing.Dict[health_domain.TelemetryAlertCategory, int] = {}
        self._last_error: str | None = None

    def record_queue_level(self, name: str, level: int, capacity: int) -> None:
        """记录某个导出队列的水位。

        :param name: 队列名（如 ``batch_log``）
        :param level: 当前积压条数
        :param capacity: 队列容量
        """
        with self._lock:
            self._queue_levels[name] = (int(level), int(capacity))
            if capacity > 0 and level >= capacity * _QUEUE_WARN_RATIO:
                self._alerts[health_domain.TelemetryAlertCategory.QUEUE_NEAR_CAPACITY] = (
                    self._alerts.get(health_domain.TelemetryAlertCategory.QUEUE_NEAR_CAPACITY, 0) + 1
                )
            if capacity > 0 and level >= capacity:
                self._alerts[health_domain.TelemetryAlertCategory.QUEUE_OVERFLOWED] = (
                    self._alerts.get(health_domain.TelemetryAlertCategory.QUEUE_OVERFLOWED, 0) + 1
                )

    def record(self, category: health_domain.TelemetryAlertCategory) -> None:
        """记录一次告警。"""
        with self._lock:
            self._alerts[category] = self._alerts.get(category, 0) + 1

    def record_error(self, message: str) -> None:
        """记录一次内部异常。"""
        with self._lock:
            self._alerts[health_domain.TelemetryAlertCategory.INTERNAL_ERROR] = (
                self._alerts.get(health_domain.TelemetryAlertCategory.INTERNAL_ERROR, 0) + 1
            )
            self._last_error = message

    def queue_levels(self) -> typing.Dict[str, typing.Tuple[int, int]]:
        """取各队列的 (水位, 容量) 快照。"""
        with self._lock:
            return dict(self._queue_levels)

    def alerts(self) -> typing.Dict[str, int]:
        """取各告警类别的发生次数。"""
        with self._lock:
            return {category.value: count for category, count in self._alerts.items()}

    def last_error(self) -> str | None:
        """取最近一次内部异常信息。"""
        with self._lock:
            return self._last_error

    def status(self) -> health_domain.HealthStatus:
        """按当前状态给出整体健康结论。

        队列溢出或端点不可达 → unhealthy；仅接近容量上限 → degraded。
        """
        with self._lock:
            alerts = set(self._alerts)
        if health_domain.TelemetryAlertCategory.QUEUE_OVERFLOWED in alerts:
            return health_domain.HealthStatus.UNHEALTHY
        if health_domain.TelemetryAlertCategory.ENDPOINT_UNREACHABLE in alerts:
            return health_domain.HealthStatus.UNHEALTHY
        if health_domain.TelemetryAlertCategory.QUEUE_NEAR_CAPACITY in alerts:
            return health_domain.HealthStatus.DEGRADED
        return health_domain.HealthStatus.HEALTHY

    def reset(self) -> None:
        """清空所有记录。"""
        with self._lock:
            self._queue_levels.clear()
            self._alerts.clear()
            self._last_error = None


_health_monitor = HealthMonitor()
_health_module_lock = threading.Lock()


def get_health_monitor() -> HealthMonitor:
    """取全局健康监控器。"""
    return _health_monitor


def get_health_module() -> HealthMonitor:
    """取全局健康监控器（``get_health_monitor`` 的同义入口）。

    两个名字都在调用方里出现过（``event_sink`` 用了两个），
    保留两个入口比改调用点更安全。
    """
    return _health_monitor


def reset_health_module() -> None:
    """重置健康状态（测试与重新初始化用）。"""
    with _health_module_lock:
        _health_monitor.reset()


__all__ = [
    "HealthMonitor",
    "get_health_module",
    "get_health_monitor",
    "reset_health_module",
]
