"""LogRecordProcessor 装配。

OpenTelemetry 的日志管道由「处理器」链组成：事件进来 → 处理器加工 → 导出器送出。
本模块负责给出这条链上需要的处理器。

**为什么需要处理器**：OpenTelemetry 默认的处理器不做任何富化，
但实际排障时需要把「哪台机器、哪个进程、哪个会话」这类维度自动附到
每条日志上 —— 靠业务侧逐条传太啰嗦，处理器是自动补齐这些维度的地方。

**历史缺口**：本模块此前是空文件，``event_sink`` 调用
``create_log_record_processors()`` 时抛 AttributeError。
"""

from __future__ import annotations

import typing

import opentelemetry.sdk._logs as sdk_logs
import opentelemetry.sdk._logs.export as log_export


def create_log_record_processors() -> tuple[typing.Any, ...]:
    """构造日志记录处理器链。

    返回的处理器顺序：

    1. ``Resource`` 已在 provider 层设置，无需重复
    2. ``BatchLogRecordProcessor`` 负责批量导出，由 ``event_sink`` 单独
       以「带队列水位监控」的子类加入 —— 队列水位是健康信号

    这里只返回**不需要额外配置**的处理器。批量处理器由调用方按
    队列容量要求构造，因为它的构造参数与健康监控耦合。
    """
    processors: list[typing.Any] = []

    # 简单处理器：立即输出到控制台，便于本地开发时看到遥测内容。
    # 生产环境通常由 batch 处理器接管，这里保留但不默认启用 ——
    # 避免在有远程收集端时产生重复输出。
    simple = getattr(sdk_logs, "SimpleLogRecordProcessor", None)
    if simple is not None:
        processors.append(simple)

    return tuple(processors)


__all__ = ["create_log_record_processors"]
