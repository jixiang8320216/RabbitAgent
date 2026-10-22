"""插件运行时的领域端口。

``PluginRuntimePort`` 描述「当前请求上下文里的插件运行时」应具备的形状。

为什么是 Protocol 而不是具体类：
    插件运行时由应用装配层创建（``plugin_manager.PluginManager``），
    而 ``request_context`` 只需要在类型标注上知道它的形状 —— 用 Protocol
    可以让上下���模块不 import 具体的插件实现，避免装配层与应用层成环。

本模块只放**结构契约**，不含任何实现。
"""

from __future__ import annotations

import typing


@typing.runtime_checkable
class PluginRuntimePort(typing.Protocol):
    """插件运行时端口。

    形状依据 ``domains.plugin.application.request_context`` 的用法反推：
    该模块把运行时实例放进 ``ContextVar``，供插件 hook 桥接器在**当前请求**
    范围内取用。槽位未绑定时读到 ``None`` 是受支持的正常情形。
    """

    def list_plugins(self) -> typing.Sequence[typing.Any]:
        """列出当前已加载的插件。"""
        ...

    def get_plugin(self, name: str) -> typing.Any | None:
        """按名取插件；不存在返回 None。"""
        ...


__all__ = ["PluginRuntimePort"]