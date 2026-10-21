"""中断协调。

负责回答两个问题：

1. **某会话当前是否被要求中断？**（``check_interrupt``）——供 ReAct 循环、
   hook、tool 执行前的风险检查点轮询。
2. **某会话当前的轮次作用域是什么？**（``get_turn_scope``）——供上层在需要
   主动取消时拿到句柄。

同时维护「会话 -> 当前 turn scope」的注册表（``register_turn`` /
``clear_turn``），生命周期与一次用户消息的 turn 对齐：
``SessionTurnExecutor`` 在开始处注册、在 ``finally`` 中清理。

实现要点：

- 会话与轮次都是并发的，因此状态放在进程级字典里并用锁保护；
- **所有查询方法都不得抛异常**：它们会被 hook、tool 等旁路能力在热路径上
  调用，抛错会污染主流程。因此本类只做「保守返回值」，绝不校验入参类型。
- 中断标记是**一次性**的：``check_interrupt`` 读取后即清除，避免同一次用户
  中断在后续多个 turn 上持续生效。

位置说明：本模块的早期实现位于外层包 ``confirmation``，现下沉到本包——``cli``
与 ``app`` 两侧都要用它，而外层 ``confirmation`` 依赖本包，反向依赖会形成环。
``confirmation.interrupt_manager`` 保留为再导出，保证旧调用点拿到同一个类。
"""

from __future__ import annotations

import logging
import threading
import typing

logger = logging.getLogger(__name__)

__all__ = ["InterruptManager"]


class InterruptManager:
    """进程级会话中断状态管理（全静态方法）。"""

    _lock = threading.RLock()
    # 会话 -> 是否被要求中断（一次性标记）
    _interrupt_flags: dict[str, bool] = {}
    # 会话 -> 当前轮次作用域
    _turn_scopes: dict[str, typing.Any] = {}
    # 会话 -> 会话级作用域（本会话所有轮次的父作用域）
    _session_scopes: dict[str, typing.Any] = {}
    # 会话 -> 本会话已完成的轮次数（仅用于诊断/日志）
    _turn_counts: dict[str, int] = {}

    # --- 中断标记 ---

    @classmethod
    def request_interrupt(cls, session_id: str | None) -> None:
        """请求中断指定会话的当前轮次。"""
        key = _session_key(session_id)
        if key is None:
            return
        with cls._lock:
            cls._interrupt_flags[key] = True

    @classmethod
    def check_interrupt(cls, session_id: str | None) -> bool:
        """查询并**消费**中断标记。

        :return: 该会话当前是否被要求中断。会话标识为空时返回 ``False``。
        """
        key = _session_key(session_id)
        if key is None:
            return False
        with cls._lock:
            return cls._interrupt_flags.pop(key, False)

    @classmethod
    def peek_interrupt(cls, session_id: str | None) -> bool:
        """查询中断标记但**不消费**它。"""
        key = _session_key(session_id)
        if key is None:
            return False
        with cls._lock:
            return cls._interrupt_flags.get(key, False)

    @classmethod
    async def clear_interrupt(cls, session_id: str | None) -> None:
        """清除指定会话的中断标记。"""
        key = _session_key(session_id)
        if key is None:
            return
        with cls._lock:
            cls._interrupt_flags.pop(key, None)

    # --- 会话级作用域 ---

    @classmethod
    def get_session_scope(cls, session_id: str | None) -> typing.Any:
        """取得（必要时创建）某会话的会话级作用域。

        会话级作用域是本会话所有轮次作用域的**父作用域**：取消它会联动取消其下
        所有轮次；轮次自身被取消不影响它。会话标识无效时返回 ``None``（不抛错，
        让调用方仍能构造出一个「无父级」的作用域）。
        """
        key = _session_key(session_id)
        if key is None:
            return None
        with cls._lock:
            scope = cls._session_scopes.get(key)
            if scope is not None and not scope.terminated:
                return scope
            scope = _new_session_scope(key)
            if scope is None:
                return None
            cls._session_scopes[key] = scope
            return scope

    @classmethod
    def clear_session_scope(cls, session_id: str | None) -> bool:
        """清除某会话的会话级作用域（会话结束时使用）。"""
        key = _session_key(session_id)
        if key is None:
            return False
        with cls._lock:
            return cls._session_scopes.pop(key, None) is not None

    # --- 轮次作用域 ---

    @classmethod
    def register_turn(cls, session_id: str | None, turn_scope: typing.Any) -> typing.Any:
        """登记某会话的当前轮次作用域；返回被替换掉的旧作用域（可能为 None）。

        :raises ValueError: 会话标识无效——无法登记的作用域也无从被中断取回，
            这里**必须**让调用方感知（``SessionTurnExecutor`` 会把它标记为失败并
            继续向外抛），而不是静默返回一个取不回来的作用域。
        """
        key = _session_key(session_id)
        if turn_scope is None:
            return None
        if key is None:
            raise ValueError("session id is invalid")
        with cls._lock:
            previous = cls._turn_scopes.get(key)
            cls._turn_scopes[key] = turn_scope
            cls._turn_counts[key] = cls._turn_counts.get(key, 0) + 1
        return previous

    @classmethod
    def get_turn_scope(cls, session_id: str | None) -> typing.Any:
        """返回某会话的当前轮次作用域；不存在返回 ``None``。"""
        key = _session_key(session_id)
        if key is None:
            return None
        with cls._lock:
            return cls._turn_scopes.get(key)

    @classmethod
    def clear_turn(cls, session_id: str | None, turn_scope: typing.Any = None) -> bool:
        """清除某会话的轮次作用域。

        :param turn_scope: 传入时仅当它与当前登记对象**相同**才清除——防止一个
            较早结束的 turn 误删后来者登记的作用域。
        :return: 是否确实清除了。
        """
        key = _session_key(session_id)
        if key is None:
            return False
        with cls._lock:
            current = cls._turn_scopes.get(key)
            if current is None:
                return False
            if turn_scope is not None and current is not turn_scope:
                return False
            cls._turn_scopes.pop(key, None)
            return True

    # --- 诊断 ---

    @classmethod
    def active_session_ids(cls) -> tuple[str, ...]:
        """返回当前存在轮次作用域的会话标识。"""
        with cls._lock:
            return tuple(cls._turn_scopes.keys())

    @classmethod
    def turn_count(cls, session_id: str | None) -> int:
        """返回某会话累计的轮次数；未知会话返回 0。"""
        key = _session_key(session_id)
        if key is None:
            return 0
        with cls._lock:
            return cls._turn_counts.get(key, 0)

    @classmethod
    def reset(cls) -> None:
        """清空全部状态（会话结束或测试使用）。"""
        with cls._lock:
            cls._interrupt_flags.clear()
            cls._turn_scopes.clear()
            cls._session_scopes.clear()
            cls._turn_counts.clear()


def _new_session_scope(session_key: str) -> typing.Any:
    """创建一个会话级取消作用域。

    作用域类型在 ``runtime.service.cancellation``（运行时层），这里**延迟导入**：
    ``confirmation_core`` 位于更底层，若在模块顶层导入运行时层，一旦运行时层
    反过来引用本包就会形成环；延迟导入把依赖限制在真正用到的时候。
    """
    try:
        import domains.agent.domain.aggregates.cancellation as cancellation
    except Exception as exc:  # pragma: no cover - 运行时层不可用时降级
        logger.warning(
            "[INTERRUPT] cancel scope type unavailable (%s), session scope disabled", exc
        )
        return None
    return cancellation.TurnCancelScope(session_id=session_key)


def _session_key(session_id: typing.Any) -> str | None:
    """把会话标识归一为非空字符串；无效返回 ``None``。"""
    if session_id is None:
        return None
    text = str(session_id).strip()
    return text or None
