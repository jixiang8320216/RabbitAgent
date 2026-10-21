"""轮次作用域的上下文传递。

ReAct 循环、hook、tool 都在深层调用栈里，用参数一路透传取消作用域会很别扭，
因此把「当前 turn 的取消作用域」放在 contextvar 里：

- :func:`set_turn_scope_context` 在进入 agent 调用前置位，返回 token；
- :func:`get_turn_scope_context` 供任意深度的检查点取回当前作用域；
- :func:`reset_turn_scope_context` 在 ``finally`` 里按 token 复位——**必须复位**，
  否则协程复用同一个 context 时会读到上一轮的已终止作用域。

未置位时 :func:`get_turn_scope_context` 返回 ``None``，调用方按「不可取消」处理。
"""

from __future__ import annotations

import contextvars
import typing

__all__ = [
    "get_turn_scope_context",
    "reset_turn_scope_context",
    "set_turn_scope_context",
]

_turn_scope: contextvars.ContextVar[typing.Any] = contextvars.ContextVar(
    "turn_scope_context", default=None
)


def set_turn_scope_context(turn_scope: typing.Any) -> contextvars.Token:
    """置位当前轮次作用域，返回用于复位的 token。"""
    return _turn_scope.set(turn_scope)


def get_turn_scope_context() -> typing.Any:
    """取回当前轮次作用域；未置位返回 ``None``。"""
    return _turn_scope.get()


def reset_turn_scope_context(token: contextvars.Token | None) -> None:
    """按 token 复位轮次作用域；token 为空时不做任何事。"""
    if token is None:
        return
    try:
        _turn_scope.reset(token)
    except ValueError:
        # token 由别的 context 创建（跨任务/跨线程误用）——此时无法复位，
        # 直接置空比让旧作用域继续生效安全。
        _turn_scope.set(None)
