"""运行期绑定的进程级槽位。

runtime 层（hook、tool、LSP、telemetry 等）经常需要在**不持有应用对象**的
情况下访问应用级能力：投递一条消息、查询某会话是否被中断、把插件 hook 桥接
进来。直接 import ``app`` 会造成循环依赖，因此这里采用「注册槽位」模式：

    应用装配阶段 -> ``set_xxx(...)`` 写入能力
    runtime 调用点 -> ``get_xxx()`` 取出能力（可能为 None，调用方需降级）

约定：

- 所有读写都在同一把锁下进行（槽位数量少，无需细粒度锁）；
- ``get_xxx()`` 允许返回 ``None``，调用方必须处理「能力未安装」的情况；
- 槽位默认全部为空，因此 runtime 代码可以在任何装配顺序下安全 import。
"""

from __future__ import annotations

import collections.abc
import threading
import typing

from domains.agent.domain.aggregates import runtime_ports as runtime_ports

_binding_lock = threading.RLock()

_lifecycle_notifier: runtime_ports.LifecycleNotifierProtocol | None = None
_lifecycle_status_notifiers: list[runtime_ports.LifecycleStatusNotifierProtocol] = []
_message_queue_getter: collections.abc.Callable[[], typing.Any] | None = None
_interrupt_checker: typing.Any = None
_plugin_hook_bridge: typing.Any = None


# --- 生命周期通知器 ---------------------------------------------------------


def get_lifecycle_notifier() -> runtime_ports.LifecycleNotifierProtocol | None:
    """返回已安装的生命周期通知器；未安装返回 ``None``。"""
    with _binding_lock:
        return _lifecycle_notifier


def set_lifecycle_notifier(notifier: runtime_ports.LifecycleNotifierProtocol | None) -> None:
    """安装（或清除）生命周期通知器。"""
    global _lifecycle_notifier
    with _binding_lock:
        _lifecycle_notifier = notifier


def get_lifecycle_status_notifiers() -> list[runtime_ports.LifecycleStatusNotifierProtocol]:
    """返回全部生命周期状态通知器。

    注意：原实现返回的是 ``list(get_lifecycle_status_notifiers)``——把函数自身
    转成列表，既丢掉了已注册的通知器，又会在调用时抛 ``TypeError``。这里改为
    读取真实的注册列表副本。
    """
    with _binding_lock:
        return list(_lifecycle_status_notifiers)


def register_lifecycle_status_notifier(
    notifier: runtime_ports.LifecycleStatusNotifierProtocol,
) -> None:
    """追加一个生命周期状态通知器（去重：同一对象不重复注册）。"""
    with _binding_lock:
        if notifier not in _lifecycle_status_notifiers:
            _lifecycle_status_notifiers.append(notifier)


def clear_lifecycle_status_notifiers() -> None:
    """清空生命周期状态通知器列表。"""
    with _binding_lock:
        _lifecycle_status_notifiers.clear()


# --- Agent 消息队列 ---------------------------------------------------------


def get_message_queue_getter() -> collections.abc.Callable[[], typing.Any] | None:
    """返回「取消息队列」的可调用对象；未安装返回 ``None``。"""
    with _binding_lock:
        return _message_queue_getter


def set_message_queue_getter(getter: collections.abc.Callable[[], typing.Any] | None) -> None:
    """安装（或清除）「取消息队列」的可调用对象。

    传函数而不是队列实例本身，是为了避免在装配早期就强制创建队列单例。
    """
    global _message_queue_getter
    with _binding_lock:
        _message_queue_getter = getter


def get_message_queue() -> typing.Any | None:
    """便捷方法：取出消息队列实例；未安装 getter 或 getter 抛错时返回 ``None``。"""
    getter = get_message_queue_getter()
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - 槽位读取不应打断调用方
        return None


# --- 中断检查 ---------------------------------------------------------------


def get_interrupt_checker() -> typing.Any:
    """返回中断检查器；未安装返回 ``None``。"""
    with _binding_lock:
        return _interrupt_checker


def set_interrupt_checker(checker: typing.Any) -> None:
    """安装（或清除）中断检查器。

    约定接口：``check_interrupt(session_id) -> bool`` 与
    ``get_turn_scope(session_id)``。这里用鸭子类型而非 Protocol，因为调用方
    （composition 层）传入的是就地定义的适配器对象。
    """
    global _interrupt_checker
    with _binding_lock:
        _interrupt_checker = checker


# --- 插件 hook 桥接 ---------------------------------------------------------


def get_plugin_hook_bridge() -> typing.Any:
    """返回插件 hook 桥接器；未安装返回 ``None``。"""
    with _binding_lock:
        return _plugin_hook_bridge


def set_plugin_hook_bridge(bridge: typing.Any) -> None:
    """安装（或清除）插件 hook 桥接器。"""
    global _plugin_hook_bridge
    with _binding_lock:
        _plugin_hook_bridge = bridge
