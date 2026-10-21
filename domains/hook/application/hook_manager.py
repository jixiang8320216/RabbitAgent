"""旧版（legacy）Hook 管理器。

历史上的 hook 执行入口。新的 hook 运行时已迁移到 ``app/application/hook_lifecycle.py``
的 ``HookLifecycleCoordinator`` + ``app/application/hook_management.py`` 的
``HookManagermentUseCases``，本类保留下来作为兼容层：

- ``RabbitApplicationCompositionState`` 仍然会构造一个实例并挂在 ``self.hook_manager`` 上；
- 调用点（如果有）通过它读取当前快照 / 策略，而不重复持有配置。

因此本类只做「持有快照 + 按事件查询」，不承担执行、不落盘、不抛异常。
"""

from __future__ import annotations

import logging
import typing

import domains.hook as hook_package

logger = logging.getLogger(__name__)

__all__ = ["HookManager"]


class HookManager:
    """legacy hook 管理器：持有快照，提供按事件查询。

    构造时不做任何 IO；未注入快照时使用空快照，保证调用方无需判空。
    """

    __slots__ = ("_snapshot", "_policy")

    def __init__(
        self,
        snapshot: hook_package.HookSnapshot | None = None,
        policy: hook_package.HookPolicy | None = None,
        /,
        *,
        hook_snapshot: hook_package.HookSnapshot | None = None,
        hook_policy: hook_package.HookPolicy | None = None,
    ) -> None:
        self._snapshot = snapshot or hook_snapshot or hook_package.HookSnapshot.empty()
        self._policy = policy or hook_policy or hook_package.HookPolicy()

    @property
    def snapshot(self) -> hook_package.HookSnapshot:
        """当前快照。"""
        return self._snapshot

    @property
    def policy(self) -> hook_package.HookPolicy:
        """当前执行策略。"""
        return self._policy

    def update_snapshot(self, snapshot: hook_package.HookSnapshot) -> None:
        """整体替换快照；传 None 视为清空。"""
        self._snapshot = snapshot if snapshot is not None else hook_package.HookSnapshot.empty()

    def hooks_for_event(self, event: typing.Any) -> tuple[typing.Any, ...]:
        """返回指定事件下的 hook 定义；无配置时返回空元组。"""
        key = getattr(event, "value", event)
        return self._snapshot.for_event(str(key))

    def has_hooks(self, event: typing.Any | None = None) -> bool:
        """指定事件是否有 hook；不传事件时判断整体是否为空。"""
        if event is None:
            return not self._snapshot.is_empty
        return bool(self.hooks_for_event(event))

    def __repr__(self) -> str:
        return f"HookManager(hooks={not self._snapshot.is_empty})"
