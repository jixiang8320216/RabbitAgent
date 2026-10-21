"""查询级遥测追踪（application 层）。

职责
----
围绕「一次用户提问」记录生命周期：什么时候开始、跑了多久、以什么状态结束。

.. code-block:: python

    tracking.start_query_tracking(
        conversation_id=session_id,
        app_mode=self.app.app_mode,
        query_text=user_msg.get_text_context(),
        session_type=self.app.session_type,
    )
    ...
    tracking.end_query_tracking(status="ok")
    tracking.end_query_tracking(status="cancel")
    tracking.end_query_tracking(status="error", error_type=error_type)

上面的调用点全部位于 ``session_turn_execution``，且已由
``telemetry_policy_state.is_telemetry_enabled()`` 做了开关判断，**本模块不再重复
判开关**（它依赖的 ``observability.domain.policy`` 目前还是空文件，重复判只会把
一个可降级的模块变成不可导入的模块）。

为什么追踪状态放在模块里而不是对象里
------------------------------------
``end_query_tracking(status="ok")`` 这个调用**不带 conversation_id**（结束点只有
``session_id`` 可用但没往下传）。所以本模块维护一个进程内的活动查询表，结束时按
下列优先级定位：

1. 显式传入的 ``conversation_id``；
2. 当前 asyncio task 绑定的查询（同一个协程里 start/end 是最常见的情况）；
3. 最近一次 start 的查询（LIFO 兜底）。

并发多会话时 2、3 可能错配，调用方最好显式传 ``conversation_id``。

绝不抛异常
----------
遥测不能把业务轮次搞挂：所有对外方法都吞掉内部异常并降级为日志。

Sink 接线
---------
真正的上报目标由 ``telemetry_runtime`` / OpenTelemetry sink 决定，但那一层目前
还是空文件。因此本模块提供两级出口：

- :func:`set_query_event_sink` —— 显式注册 sink（推荐，等 telemetry 落地后由
  ``observability.composition.initialize_telemetry`` 调一次即可）；
- 自动探测 —— 若 ``telemetry_runtime`` 后续暴露了 sink 访问器，会被自动接管。

都没有时退化成 ``logger.debug``，功能不受影响。

关于 ``query_text``
-------------------
调用方会把用户原文传进来。是否脱敏由 sink 决定，本模块不做截断或改写——
遥测开关本身就是 `is_telemetry_enabled()` 的语义边界。
"""

from __future__ import annotations

import asyncio
import datetime as datetime_module
import itertools
import logging
import threading
import time
import typing

import pydantic

import domains.observability.application.emitter as emitter
from domains.observability.application import observability_context
import domains.observability.domain.event_models as event_models

logger = logging.getLogger(__name__)


def start_query_tracking(
    conversation_id: str,
    app_mode: str,
    query_text: str|None = None,
    *,
    session_type: object = None,
) -> None:
    """开始一次查询追踪。

    :param conversation_id: 会话标识，结束时用它定位（推荐显式回传）。
    :param app_mode: 应用模式（DELEGATE / PLAN ...），仅作维度记录。
    :param query_text: 用户原文；是否脱敏由 sink 决定。
    :param session_type: 会话类型，仅作维度记录。
    :param extra: 附加维度，非 JSON 可序列化的字段会被丢弃。
    :return: 本次查询的 ``query_id``（进程内唯一递增）。
    """
    observability_context.set_app_mode(app_mode)
    observability_context.set_session_type(session_type)
    observability_context.reset_query_stats()
    observability_context.set_query_start_time()

    input_length= len(query_text) if query_text else None
    emitter.emit_query_start(conversation_id=conversation_id, query_text= query_text, input_length=input_length)


def end_query_tracking(
    status: event_models.CancelStatusEnum = "ok",
    error_type: str | None = None,
) ->  None:
    """结束一次查询追踪并投递事件。

    :param status: 终态，调用方目前用 ``"ok"`` / ``"cancel"`` / ``"error"``。
    :param error_type: 失败时的异常类型名。
    :param conversation_id: 要结束的会话；不传则按「当前 task → 最近一次 start」定位。
    :return: 生成的事件；找不到对应的 start 时返回 ``None``。
    """
    emitter.emit_query_end(status=status, error_type=error_type)

