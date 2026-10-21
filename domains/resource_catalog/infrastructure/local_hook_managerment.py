from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .local_hook_resources import HookResources, LocalHookResourceLoader

class LocalHookConfigurationStore:
    """缓存 `LocalHookResourceLoader` 产出的 hook 配置快照，并提供按需刷新。

    构造时只持有 loader 引用，快照按需懒加载，避免与 `runtime_builder`
    里已有的 `hook_resource_loader.load()` 调用重复读盘。
    """

    def __init__(self, loader: "LocalHookResourceLoader") -> None:
        self._loader = loader
        self._resources: "HookResources | None" = None

    def current(self) -> "HookResources":
        """返回当前缓存的快照；首次访问时懒加载。"""
        if self._resources is None:
            self._resources = self._loader.load()
        return self._resources

    def refresh(self) -> "HookResources":
        """强制重新加载快照并返回最新结果。"""
        self._resources = self._loader.load()
        return self._resources

    @property
    def snapshot(self) -> dict:
        return self.current().snapshot

    @property
    def policy(self):
        return self.current().policy

    @property
    def configuration_paths(self) -> tuple:
        return self.current().configuration_paths

    @property
    def fingerprint(self) -> str:
        return self.current().fingerprint

    @property
    def diagnostics(self) -> list:
        return self.current().diagnostics
