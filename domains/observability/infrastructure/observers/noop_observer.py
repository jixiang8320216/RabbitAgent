"""空实现观察者。

观察者（observer）在整条调用链上被高频调用（每次事件回显、每次生命周期
信号），因此需要两个特性：

1. **可被安全地当作兜底默认值**——调用点形如
   ``observer if observer is not None else NoOpObserver()``，所以构造必须无参
   且零副作用。
2. **对任意事件方法都不抛 AttributeError**——真实观察者的方法集在演进，
   兜底实现不应因为「多了一个新事件」而崩溃，因此通过 ``__getattr__`` 兜住
   所有未知方法并返回一个空操作的 callable。

子类（如 skill_evolution 的 ``LifecycleObserver``）会继承本类并覆盖自己关心
的方法，其余事件继续走空操作路径。
"""

from __future__ import annotations

import typing

__all__ = ["NoOpObserver"]


def _noop(*_args: typing.Any, **_kwargs: typing.Any) -> None:
    """空操作：接收任意位置/关键字参数，什么也不做。"""
    return None


class _NoOpObserverMeta(type):
    """让空操作类也能通过 ``RabbitObserver`` 这类 Protocol 的运行时检查。

    ``RabbitObserver`` 是 ``@typing.runtime_checkable`` 的 Protocol。Protocol 的
    ``isinstance`` 只承认**真实存在于类上**的成员：实例级 ``__getattr__`` 不算数，
    单纯的类级 ``__getattr__`` 也不算数（两者均实测返回 ``False``）。于是
    ``isinstance(LifecycleObserver(...), RabbitObserver)`` 恒为 ``False``，
    ``_resolve_lifecycle_observer`` 每次都记
    ``[RABBIT APP] lifecycle_observer is unavailable``，生命周期观察者永远
    进不了 composite——一个只留ERROR 日志、却没有任何报错的功能缺失。

    这里在元类 ``__new__`` 里为每个子类补齐 Protocol 要求的成员，让它们成为
    类 ``__dict__`` 里真实存在的方法，而不是依赖 ``__getattr__`` 兜。
    """

    def __new__(
        mcls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, typing.Any],
        **kwargs: typing.Any,
    ) -> typing.Any:
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        try:
            from domains.observability.domain.rabbit_observer import RabbitObserver
        except Exception:
            return cls
        # 子类自己实现过的方法优先，绝不覆盖真实逻辑
        for attr in getattr(RabbitObserver, "__protocol_attrs__", ()):
            if attr.startswith("_") or attr in namespace:
                continue
            if any(attr in base.__dict__ for base in bases):
                continue
            setattr(cls, attr, _noop)
        return cls


class NoOpObserver(metaclass=_NoOpObserverMeta):
    """不做任何事情的观察者实现。"""

    __slots__ = ()

    def __getattr__(self, name: str) -> typing.Callable[..., None]:
        """未知属性一律按「空操作事件回调」处理。

        仅在常规属性查找失败时触发，因此不会遮蔽子类真实定义的方法；
        下划线开头的名字（``__deepcopy__``、``_ipython_canary_method_should_not_exist_``
        等）仍按常规抛 ``AttributeError``，避免破坏协议探测与拷贝逻辑。
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return _noop

    def __repr__(self) -> str:
        return "NoOpObserver()"
