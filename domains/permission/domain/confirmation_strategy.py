import abc
from typing import Optional


class ConfirmationStrategy(abc.ABC):
    """
    确认策略抽象基类。

    子类（如 ``CLIConfirmationStartegy``）实现具体的用户确认交互。
    基类提供类级注册表，供启动时注册当前活跃策略、后续业务逻辑按需取回。

    注意：方法名 ``register_startegy`` / ``get_active_startegy`` 沿用项目既定
    typo（应为 ``strategy``），与 ``tui_application.py:113`` 的调用点保持一致，
    不单独修正以免破坏调用方。
    """

    # 类级注册表：当前活跃的确认策略实例。
    _active_startegy: "Optional[ConfirmationStrategy]" = None

    @classmethod
    def register_strategy(cls, strategy: "ConfirmationStrategy") -> None:
        """注册/替换当前活跃的确认策略实例。"""
        cls._active_startegy = strategy

    @classmethod
    def get_active_startegy(cls) -> "Optional[ConfirmationStrategy]":
        """获取当前注册的确认策略；未注册返回 None。"""
        return cls._active_startegy
