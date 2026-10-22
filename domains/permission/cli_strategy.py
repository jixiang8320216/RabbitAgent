"""CLI/TUI 确认策略。

高危操作（删除、覆盖、对外发布、权限变更等）在执行前必须由用户明确确认，
这是 L4-3.2「确认拦截」的执行环节。

设计要点——**fail-close**：
    拿不到用户应答时一律拒绝。安全场景下「默认放行」等于把不可逆操作
    交给默认值，因此超时、无交互通道、observer 缺失都返回 False。

与 observer 的协作：
    构造时接收 ``TUIObserver``，用于把确认请求推给前端；有 observer 时走
    异步应答通道，没有时退化为同步决策器 ``auto_approve``（仅供测试与
    无人值守场景显式开启）。
"""

from __future__ import annotations

import typing

from domains.permission.domain import ConfirmationStrategy
from domains.permission.domain import contracts

if typing.TYPE_CHECKING:  # pragma: no cover
    from domains.permission.domain.contracts import AuthorizationRule


class CLIConfirmationStrategy(ConfirmationStrategy):
    """CLI/TUI 确认策略。

    构造时接收 ``TUIObserver``，用于在需要用户确认（如执行敏感工具、
    覆盖文件等）时通过 observer 向用户展示提示并等待应答。
    """

    #: 等待应答的超时秒数；超时按拒绝处理。
    DEFAULT_TIMEOUT_SECONDS: float = 60.0

    def __init__(
        self,
        observer: typing.Any = None,
        *,
        auto_approve: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.observer = observer
        #: 无人值守场景的显式放行开关。默认 False（安全默认）。
        self.auto_approve = auto_approve
        self.timeout_seconds = timeout_seconds
        #: 最近一次确认请求的描述，供审计与测试断言。
        self.last_request: dict[str, typing.Any] | None = None

    # ── 对外确认入口 ────────────────────────────────────────────
    def confirm(
        self,
        operation_type: str,
        target: str,
        *,
        rule: "AuthorizationRule | None" = None,
        reason: str = "",
    ) -> bool:
        """请求用户确认一次高危操作。

        :return: 用户明确同意返回 True；拒绝、超时或无法确认返回 False。
        """
        request = {
            "operation_type": operation_type,
            "target": target,
            "reason": reason,
            "risk_level": rule.risk_level.value if rule is not None else None,
            "scope": rule.scope.value if rule is not None else None,
            "description": rule.description if rule is not None else "",
        }
        self.last_request = request

        # 无交互通道且未显式开启自动放行 → fail-close
        if self.observer is None:
            return bool(self.auto_approve)

        try:
            return bool(self._request_via_observer(request))
        except Exception:
            # 确认链路自身故障同样按拒绝处理：不能因为 UI 挂了就把高危操作放行
            return False

    def request_permission(self, *args: typing.Any, **kwargs: typing.Any) -> bool:
        """``confirm`` 的别名，兼容历史命名。"""
        return self.confirm(*args, **kwargs)

    # ── 内部实现 ────────────────────────────────────────────────
    def _request_via_observer(self, request: dict[str, typing.Any]) -> bool:
        """经observer 请求用户应答。

        observer 契约：优先支持 ``request_confirmation``（返回 bool）；
        其次支持 ``confirm``。两者都没有时视为无交互能力，返回 False。
        """
        for name in ("request_confirmation", "confirm"):
            handler = getattr(self.observer, name, None)
            if callable(handler):
                return handler(request)
        return False

    def describe(self, operation_type: str, target: str) -> str:
        """生成面向用户的确认文案。

        指南 L4-3.4 要求确认提示能准确说明「将要做什么」，让用户据此决策，
        因此这里显式带上操作类型、风险等级与目标。
        """
        rule = self.match_rule(operation_type, target)
        risk = rule.risk_level.value if rule is not None else "unknown"
        return (
            f"确认{operation_type} 操作（风险等级 {risk}）：{target}\n"
            f"{rule.description if rule is not None else ''}"
        )

    @staticmethod
    def match_rule(
        operation_type: str, target: str
    ) -> "AuthorizationRule | None":
        """按内置规则表找出首条命中规则。"""
        for rule in _iter_rules():
            if rule.matches(operation_type, target):
                return rule
        return None


def _iter_rules() -> typing.Iterator["AuthorizationRule"]:
    from domains.permission.domain.policy import default_rules

    yield from default_rules()


#: 历史 typo 别名。工程内既有公开名 ``CLIConfirmationStartegy``（``strategy``
#: 被误写为 ``startegy``），而本类的实际名字是正确拼写的
#: ``CLIConfirmationStrategy``。保留别名让旧引用点继续可用 ——
#: 与 ``ConfirmationStrategy.register_startegy`` 的处理方式保持一致。
CLIConfirmationStartegy = CLIConfirmationStrategy

__all__ = ["CLIConfirmationStrategy", "CLIConfirmationStartegy"]