"""遥测事件投递出口（application 层）。

目前真正的 sink（``telemetry_runtime`` / OpenTelemetry exporter）还是空文件，
所以这里保持**无副作用的空投递**：签名与调用方严格对齐，等 sink 落地后只需
在本模块内部接上，调用点一行都不用改。

调用方见 ``observability.application.tracking``：

- ``emit_query_start(conversation_id=..., query_text=..., input_length=...)``
- ``emit_query_end(status=..., error_type=...)``
"""

from __future__ import annotations

import logging
import typing

logger = logging.getLogger(__name__)


import logging

logger = logging.getLogger(__name__)


async def emit_tool_execute(*args, **kwargs)->None:
    pass


def emit_query_start(
    *,
    conversation_id: str,
    query_text: str | None = None,
    input_length: int | None = None,
) -> None:
    """查询开始事件。当前无 sink，降级为 debug 日志。

    注意：``tracking`` 模块是**同步**调用本函数的，因此这里必须是同步函数，
    不能写成 ``async def``（否则会被当作未 await 的协程空跑，事件永不发出）。
    """
    logger.debug(
        "[telemetry] query start: conversation_id=%s input_length=%s",
        conversation_id,
        input_length,
    )


def emit_query_end(
    *,
    status: str = "ok",
    error_type: str | None = None,
) -> None:
    """查询结束事件。当前无 sink，降级为 debug 日志（同步，同上）。"""
    logger.debug(
        "[telemetry] query end: status=%s error_type=%s",
        status,
        error_type,
    )


def emit_query_start(
    *,
    conversation_id: str | None = None,
    query_text: str | None = None,
    input_length: int | None = None,
    **extra: typing.Any,
) -> None:
    """一次用户查询开始。

    空投递：只留 debug 痕迹，方便在未接 sink 时确认链路已被走到。
    """
    logger.debug(
        "query start: conversation_id=%s input_length=%s",
        conversation_id,
        input_length,
    )


def emit_agent_start(
    *,
    agent_id: str | None = None,
    agent_name: str | None = None,
    skill_name: str | None = None,
    role: typing.Any = None,
    parent_id: str | None = None,
    **extra: typing.Any,
) -> None:
    """一次 agent 调用开始（``agent_call_lifecycle_hooks._emit_start_telemetry``）。"""
    logger.debug("agent start: name=%s skill=%s parent=%s", agent_name, skill_name, parent_id)


def emit_agent_finish(
    *,
    agent_id: str | None = None,
    agent_name: str | None = None,
    skill_name: str | None = None,
    role: typing.Any = None,
    parent_id: str | None = None,
    status: str = "ok",
    tool_call_count: int = 0,
    duration_ms: int = 0,
    error_type: str | None = None,
    **extra: typing.Any,
) -> None:
    """一次 agent 调用结束。"""
    logger.debug(
        "agent finish: name=%s status=%s duration_ms=%s",
        agent_name,
        status,
        duration_ms,
    )


def emit_query_end(
    *,
    status: str = "ok",
    error_type: str | None = None,
    **extra: typing.Any,
) -> None:
    """一次用户查询结束（``ok`` / ``cancel`` / ``error``）。"""
    logger.debug("query end: status=%s error_type=%s", status, error_type)
