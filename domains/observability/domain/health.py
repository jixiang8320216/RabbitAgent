"""遥测健康告警的领域模型。

只定义「有哪些告警类别」——具体的监控状态、阈值判定在应用层。

**历史缺口**：本模块此前是空文件，而 ``event_sink`` 需要用它来声明
「远程端点不可达」这类告警，缺失时会在异常处理路径上抛 AttributeError。
"""

from __future__ import annotations

import enum


class TelemetryAlertCategory(enum.StrEnum):
    """遥测链路的告警类别。

    类别用于**分诊**：出问题时先看是「本地队列积压」还是「远程端点不可达」，
    两者的处置动作完全不同。
    """

    #: 远程导出端点不可达（网络、证书、鉴权等）
    ENDPOINT_UNREACHABLE = "endpoint_unreachable"
    #: 本地队列接近容量上限，可能开始丢数据
    QUEUE_NEAR_CAPACITY = "queue_near_capacity"
    #: 本地队列已溢出，数据被丢弃
    QUEUE_OVERFLOWED = "queue_overflowed"
    #: 采集端内部异常
    INTERNAL_ERROR = "internal_error"


class HealthStatus(enum.StrEnum):
    """整体健康状态。"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


__all__ = ["HealthStatus", "TelemetryAlertCategory"]
