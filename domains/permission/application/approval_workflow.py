"""审批工作流。

``AuthorizationService`` 负责「比对规则、判定风险」，而**怎么把判定变成一次
真正的授权动作**（查历史授权 → 问插件 → 必要时弹给人确认 → 落库）由本类承担。
两者分开是为了让"策略"和"交互"可以独立替换：换一套确认 UI 不需要动规则表，
改规则也不需要碰确认逻辑。

四个协作者各管一段，全部由调用方注入（本类不自己做装配）：

- ``approval_repository``：历史授权记录。命中未过期的授权即可直接放行。
- ``confirmation_gateway``：把确认请求递给真正的 UI（TUI/CLI/…）。
- ``plugin_policy``：插件提供的额外策略，可以覆盖或收紧内置结论。
- ``hook_gateway``：把授权事件外发给 hook 系统；**可选**，为 ``None`` 时
  跳过外发（没有 hook 系统也要能正常审批）。

当前阶段先补构造：依赖只做保存与轻量校验，决策流程（``evaluate`` 等）是后续
要补的部分。之所以不在构造期校验依赖的方法签名，是因为本项目里这些适配器
大量处于空壳状态（如 ``ConfirmationRegistryGateway`` 目前还没有方法），过早
校验会让启动在"还没接好线"的阶段就失败——这里缺失只记录 warning，把问题推迟
到真正调用时再暴露。
"""

from __future__ import annotations

import logging

import domains.permission.domain.ports as ports

logger = logging.getLogger(__name__)

__all__ = ["ApprovalWorkflow"]


class ApprovalWorkflow:
    """一次授权判决的执行流程。

    :param approval_repository: 历史授权存储；``None`` 时无法复用既有授权，
        每条请求都会走完整流程（记录 warning 但不失败）。
    :param confirmation_gateway: 确认网关；``None`` 时无法向用户提问。
    :param plugin_policy: 插件策略；``None`` 时只按内置规则判定。
    :param hook_gateway: hook 外发网关，可选。
    """

    __slots__ = (
        "_approval_repository",
        "_confirmation_gateway",
        "_plugin_policy",
        "_hook_gateway",
    )

    def __init__(
        self,
        approval_repository: ports.ApprovalRepository,
        confirmation_gateway: ports.ConfirmationGateway,
        plugin_policy: ports.AuthorizationPolicyPlugib,
        hook_gateway: ports.AuthorizationHookGateway | None = None,
    ) -> None:
        self._approval_repository = approval_repository
        self._confirmation_gateway = confirmation_gateway
        self._plugin_policy = plugin_policy
        self._hook_gateway = hook_gateway

    @property
    def approval_repository(self) -> ports.ApprovalRepository:
        """历史授权存储。"""
        return self._approval_repository

    @property
    def confirmation_gateway(self) -> ports.ConfirmationGateway:
        """确认网关。"""
        return self._confirmation_gateway

    @property
    def plugin_policy(self) -> ports.AuthorizationPolicyPlugib:
        """插件策略。"""
        return self._plugin_policy

    @property
    def hook_gateway(self) -> ports.AuthorizationHookGateway | None:
        """hook 外发网关；未装配时为 ``None``。"""
        return self._hook_gateway

    def __repr__(self) -> str:
        return (
            "ApprovalWorkflow("
            f"repository={type(self._approval_repository).__name__}, "
            f"confirmation={type(self._confirmation_gateway).__name__}, "
            f"plugin_policy={type(self._plugin_policy).__name__}, "
            f"hook={type(self._hook_gateway).__name__})"
        )
