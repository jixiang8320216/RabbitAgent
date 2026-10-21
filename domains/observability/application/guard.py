"""遥测埋点的"护栏"：把一段执行包进计时上下文。

用法（调用方分布在 ``provider_chat_model`` 与 ``runtime_observer_bridge``）：

.. code-block:: python

    with guard.telemetry_guard("_send_thinking_start"):
        ...
    with guard.telemetry("on_thinking_start"):
        ...

为什么存在
----------
模型调用链上的每一步都要能被计时、能被观察，但**观察动作本身绝不能改变
业务行为**。把这段逻辑集中在一个模块里，业务侧就只需要一个 ``with``，不用
各自写 try/finally。

绝不吞异常
----------
这是本模块唯一的硬约束。护栏包裹的是真实的模型调用，如果这里把异常吃掉，
上层看到的会是"模型没回话"而不是真正的报错——排查成本极高。因此异常一律
原样抛出，只在抛出前记一行日志。

真正的上报 sink（OpenTelemetry exporter）目前还没落地，所以这里退化为
logger.debug：链路完整可用，数据暂不入仓。
"""

from __future__ import annotations

import contextlib
import logging
import time
import typing

logger = logging.getLogger(__name__)

# 超过这个毫秒数才值得记一行日志，避免高频埋点把日志刷爆
SLOW_CALL_THRESHOLD_MS = 200.0


@contextlib.contextmanager
def telemetry(name: str, **dimensions: typing.Any) -> typing.Iterator[None]:
    """计时一段执行；异常原样抛出。"""
    started = time.perf_counter()
    try:
        yield
    except BaseException:
        logger.debug(
            "telemetry span %s raised after %.1fms",
            name,
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= SLOW_CALL_THRESHOLD_MS:
            logger.debug("telemetry span %s took %.1fms", name, elapsed_ms)


@contextlib.contextmanager
def telemetry_guard(name: str, **dimensions: typing.Any) -> typing.Iterator[None]:
    """``telemetry`` 的同义入口。

    两个名字在调用方里都在用（``telemetry`` 偏 observer 侧、``telemetry_guard``
    偏 provider 侧），语义一致，保留两个入口比改一堆调用点更安全。
    """
    with telemetry(name, **dimensions):
        yield
