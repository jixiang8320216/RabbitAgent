"""投递上下文：当前用户消息的元数据。

异步投递（``async_delivery_ids`` 对应的后台交付、hook 里的延迟消息）发生时，
原始调用栈早已结束，只能从上下文里取回「这条消息原本带什么元数据」。
"""

from __future__ import annotations

import contextvars
import typing

__all__ = [
    "bind_user_message_metadata",
    "clear_user_message_metadata",
    "get_user_message_metadata",
    "reset_user_message_metadata",
]

_user_message_metadata: contextvars.ContextVar[typing.Any] = contextvars.ContextVar(
    "user_message_metadata", default=None
)



def bind_user_message_metadata(metadata: typing.Any) -> contextvars.Token:
    """绑定当前用户消息元数据，返回用于复位的 token。"""
    return _user_message_metadata.set(metadata)


def get_user_message_metadata() -> typing.Any:
    """取回当前用户消息元数据；未绑定返回 ``None``。"""
    return _user_message_metadata.get()


def reset_user_message_metadata(token: contextvars.Token | None) -> None:
    """按 token 复位；token 为空时不做任何事。"""
    if token is None:
        return
    try:
        _user_message_metadata.reset(token)
    except ValueError:
        _user_message_metadata.set(None)

def clear_user_message_metadata() -> None:
    """直接清空（调用方没保存 token 时的兜底复位）。"""
    _user_message_metadata.set(None)

_scheduled_delivery: contextvars.ContextVar[typing.Any] = contextvars.ContextVar(
    "scheduled_delivery", default=None,
)
def clear_scheduled_delivery_context() -> None:
    _scheduled_delivery.set(None)


