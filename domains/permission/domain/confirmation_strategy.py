import abc
import typing
from typing import Optional

if typing.TYPE_CHECKING:  # pragma: no cover
    from domains.permission.domain.contracts import AuthorizationRule


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

    def confirm(
        self,
        operation_type: str,
        target: str,
        *,
        rule: "Optional[AuthorizationRule]" = None,
        reason: str = "",
    ) -> bool:
        """请求用户确认一次高危操作。

        基类默认**拒绝**：安全场景必须 fail-close——拿不到用户应答时
        放行等于把不可逆操作交给默认值。子类应覆盖为真实的交互确认。

        :param operation_type: 操作类型字符串（如 ``shell_command``）。
        :param target: 待确认的目标（命令串、文件路径等）。
        :param rule: 命中的授权规则，可用于展示风险等级与作用域。
        :param reason: 触发确认的原因说明。
        :return: 用户明确同意返回 True；拒绝或无法确认返回 False。
        """
        return False

    def request_permission(self, *args: typing.Any, **kwargs: typing.Any) -> bool:
        """``confirm`` 的别名，兼容历史命名。"""
        return self.confirm(*args, **kwargs)
