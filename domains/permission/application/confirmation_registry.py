"""确认策略注册表。

启动装配阶段把当前使用的确认策略（CLI / TUI）登记进来，业务代码在需要用户
确认时按需取回。真正的策略抽象在
:class:`~confirmation_core.domain.confirmation_strategy.ConfirmationStrategy`，
本类是它的**应用侧门面**：策略实例的存放位置属于应用装配细节，领域层只关心
抽象接口，因此注册表放在 ``application`` 层、向下依赖 ``domain``。

方法同时提供正确拼写（``register_strategy``）与项目历史 typo 拼写
（``register_startegy``）——```` 与历史调用点两种写法都在用，
这里用别名兼容，避免在调用点逐个改名。
"""

from __future__ import annotations

import threading
import typing

from domains.permission.domain.confirmation_strategy import ConfirmationStrategy

__all__ = ["ConfirmationRegistry", "ConfirmationStrategy"]


class ConfirmationRegistry:
    """确认策略的进程级注册表（全静态方法）。"""

    _lock = threading.RLock()
    # 名称 -> 策略实例
    _strategies: dict[str, ConfirmationStrategy] = {}
    # 当前活跃策略名称
    _active_name: str | None = None

    # --- 注册 ---

    @classmethod
    def register_strategy(
        cls,
        strategy: ConfirmationStrategy | None,
        name: str | None = None,
    ) -> None:
        """登记一个确认策略；``name`` 省略时使用策略类名。

        首次登记的策略自动成为活跃策略；之后只有显式登记同名策略才会切换。
        """
        if strategy is None:
            return
        key = _strategy_name(strategy, name)
        with cls._lock:
            cls._strategies[key] = strategy
            if cls._active_name is None:
                cls._active_name = key
        # 与领域层类级注册表保持同步，避免「两处各存一份、取回的不是同一个」
        ConfirmationStrategy.register_strategy(strategy)

    @classmethod
    def register_startegy(
        cls,
        strategy: ConfirmationStrategy | None,
        name: str | None = None,
    ) -> None:
        """``register_strategy`` 的历史 typo 别名。"""
        cls.register_strategy(strategy, name=name)

    # --- 取回 ---

    @classmethod
    def get_strategy(cls, name: str | None = None) -> ConfirmationStrategy | None:
        """按名称取回策略；``name`` 为空时返回当前活跃策略。"""
        with cls._lock:
            if name:
                return cls._strategies.get(name)
            if cls._active_name is None:
                return None
            return cls._strategies.get(cls._active_name)

    @classmethod
    def get_active_strategy(cls) -> ConfirmationStrategy | None:
        """返回当前活跃策略；未登记时回落到领域层类级注册表。"""
        strategy = cls.get_strategy()
        if strategy is not None:
            return strategy
        return ConfirmationStrategy.get_active_startegy()

    @classmethod
    def get_active_startegy(cls) -> ConfirmationStrategy | None:
        """``get_active_strategy`` 的历史 typo 别名。"""
        return cls.get_active_strategy()

    @classmethod
    def set_active(cls, name: str) -> bool:
        """切换活跃策略；名称未登记返回 ``False``。"""
        with cls._lock:
            if name not in cls._strategies:
                return False
            cls._active_name = name
            return True

    # --- 诊断 / 清理 ---

    @classmethod
    def registered_names(cls) -> tuple[str, ...]:
        with cls._lock:
            return tuple(cls._strategies.keys())

    @classmethod
    def clear(cls) -> None:
        """清空注册表（测试或会话销毁时使用）。"""
        with cls._lock:
            cls._strategies.clear()
            cls._active_name = None


def _strategy_name(strategy: typing.Any, name: str | None) -> str:
    """确定策略的登记名称：显式名称优先，其次类名。"""
    if name:
        return name
    return type(strategy).__name__
