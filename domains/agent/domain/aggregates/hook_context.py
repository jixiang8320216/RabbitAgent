"""Hook 轮次标识上下文。

hook 在一次 turn 内可能被执行多次（每次工具调用前后），而每次执行的「hook 轮次
标识」要独立——``SessionTurnExecutor`` 在调用 agent 前会新生成一个 uuid 覆盖它，
退出时再恢复上一轮的值，因此这里提供「读旧值 + 置新值 + 按 token 复位」三个动作。
"""

from __future__ import annotations

import contextvars

__all__ = ["get_hook_turn_id", "reset_hook_turn_id", "set_hook_turn_id"]

_hook_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hook_turn_id", default=None
)


def get_hook_turn_id() -> str | None:
    """取回当前 hook 轮次标识；未置位返回 ``None``。"""
    return _hook_turn_id.get()


def set_hook_turn_id(turn_id: str | None) -> contextvars.Token:
    """置位 hook 轮次标识，返回用于复位的 token。"""
    return _hook_turn_id.set(turn_id)


def reset_hook_turn_id(token: contextvars.Token | None) -> None:
    """按 token 复位；token 为空时不做任何事。"""
    if token is None:
        return
    try:
        _hook_turn_id.reset(token)
    except ValueError:
        _hook_turn_id.set(None)
