"""轮次标识（turn id）的上下文传递。

与 :mod:`runtime.service.scope_context` 同理：turn id 会被持久化、hook、日志在
深层调用栈里读取，用 contextvar 传递比逐层透传更合适。

``turn_id`` 与「消息 id」不是一回事：消息 id 属于 ``Msg``，turn id 由宿主的
``_prepare_turn_identity`` 决定，一次 turn 可能对应多条消息。
"""

from __future__ import annotations

import contextvars

__all__ = ["get_turn_id", "reset_turn_id", "set_turn_id"]

_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "turn_identity", default=None
)


def set_turn_id(turn_id: str | None) -> contextvars.Token:
    """置位当前轮次标识，返回用于复位的 token。"""
    return _turn_id.set(turn_id)


def get_turn_id() -> str | None:
    """取回当前轮次标识；未置位返回 ``None``。"""
    return _turn_id.get()


def reset_turn_id(token: contextvars.Token | None) -> None:
    """按 token 复位轮次标识；token 为空时不做任何事。"""
    if token is None:
        return
    try:
        _turn_id.reset(token)
    except ValueError:
        _turn_id.set(None)
