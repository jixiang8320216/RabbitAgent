"""权限域的领域端口。

本模块放两类端口：

- ``ApprovalRepository``：历史授权记录存储。
- ``AuthorizationRuleProvider``：授权规则来源。

用途（L4-3.2 确认链路）：
    用户对某类高危操作授权过一次后，同类操作在授权有效期内可直接放行，
    不必每次都打断用户。授权记录的读写都通过本端口进行。

为什么是 Protocol：
    具体实现是 ``domains.permission.infrastructure.file_approval_repository``
    与 ``yaml_rule_provider``。领域层只依赖形状，不依赖存储介质 —— 换成数据库
    或远程存储时不需要改领域代码。
"""

from __future__ import annotations

import typing


@typing.runtime_checkable
class ApprovalRepository(typing.Protocol):
    """历史授权记录存储端口。"""

    def get_valid_approval(
        self,
        *,
        session_id: str,
        operation_type: str,
        target: str,
        now: typing.Any = None,
    ) -> typing.Any | None:
        """取一条仍然有效的授权记录。

        :param session_id: 授权所属会话。
        :param operation_type: 操作类型。
        :param target: 操作目标。
        :param now: 判定「是否过期」的时间基准；为 None 时由实现取当前时间。
        :return: 有效授权记录；不存在或已过期返回 None。
        """
        ...

    def record_approval(
        self,
        *,
        session_id: str,
        operation_type: str,
        target: str,
        granted: bool,
        reason: str = "",
        expires_at: typing.Any = None,
    ) -> None:
        """记录一次授权结果（同意或拒绝）。"""
        ...


@typing.runtime_checkable
class AuthorizationRuleProvider(typing.Protocol):
    """授权规则来源端口。

    内置规则来自 ``domains.permission.domain.policy.default_rules()``；
    本端口用于**追加**来自外部配置（YAML）的规则，让部署方无需改代码即可
    收紧或放宽策略。
    """

    def rules(self) -> typing.Sequence[typing.Any]:
        """返回该来源提供的授权规则序列。"""
        ...

    def reload(self) -> None:
        """重新加载规则来源（配置文件变更后调用）。"""
        ...


@typing.runtime_checkable
class ConfirmationGateway(typing.Protocol):
    """确认网关端口：把授权/确认请求递交给真实 UI（TUI/CLI/...）。"""

    def request_confirmation(
        self,
        *,
        operation_type: str,
        target: str,
        reason: str = "",
    ) -> bool:
        """向用户发起一次确认；返回用户是否同意。"""
        ...


@typing.runtime_checkable
class AuthorizationHookGateway(typing.Protocol):
    """授权事件外发网关端口（可选；为 ``None`` 时跳过外发）。"""

    def emit(self, event: typing.Any) -> None:
        """外发一次授权事件。"""
        ...


@typing.runtime_checkable
class AuthorizationPolicyPlugin(typing.Protocol):
    """插件侧授权策略端口：可覆盖或收紧内置结论。"""

    def evaluate(
        self,
        *,
        operation_type: str,
        target: str,
        context: typing.Any = None,
    ) -> typing.Any:
        """返回插件侧策略结论（``True``/``False``/``None`` 表示不影响）。"""
        ...


@typing.runtime_checkable
class AuthorizationContextProvider(typing.Protocol):
    """授权判定所需上下文的提供端口。"""

    def provide(self, *, session_id: str) -> typing.Any:
        """返回授权判定所需的会话上下文。"""
        ...


__all__ = [
    "ApprovalRepository",
    "AuthorizationRuleProvider",
    "ConfirmationGateway",
    "AuthorizationHookGateway",
    "AuthorizationPolicyPlugin",
    "AuthorizationContextProvider",
]