"""观察者组合。

同一个事件往往需要多个消费者：TUI/CLI 要回显、skill_evolution 要发心跳、
后续可能还有 telemetry。与其在每个调用点遍历列表，不如在装配阶段把它们
组合成一个 :class:`CompositeRabbitObserver`，让调用点看到的仍然是「一个
观察者」。

核心语义：

- **转发所有事件**：无法枚举全部事件名（观察者协议在演进），因此通过
  ``__getattr__`` 动态返回「把同名方法依次转发给每个子观察者」的闭包。
- **子观察者异常隔离**：单个子观察者抛错不应中断其它观察者，也不应让用户
  的请求失败。异常被记录为 warning 后吞掉——观察属于旁路能力。
- **返回最后一个非 None 结果**：多数事件回调返回 None；若某个子观察者返回
  了值（例如「是否已处理」），保留最后一个非 None 值作为聚合结果。
"""

from __future__ import annotations

import logging
import typing

logger = logging.getLogger(__name__)

__all__ = ["CompositeRabbitObserver"]


class CompositeRabbitObserver:
    """把多个观察者聚合成一个。

    :param observers: 子观察者序列；``None`` 项会被忽略。
    """

    __slots__ = ("_observers", "_closed")

    def __init__(self, observers: typing.Iterable[typing.Any] | None = None) -> None:
        self._observers: tuple[typing.Any, ...] = tuple(obs for obs in (observers or ()) if obs is not None)
        self._closed = False

    @property
    def observers(self) -> tuple[typing.Any, ...]:
        """当前生效的子观察者。"""
        return self._observers

    def add(self, observer: typing.Any) -> None:
        """追加一个子观察者（None 忽略）。"""
        if observer is None:
            return
        self._observers = (*self._observers, observer)

    def close(self) -> None:
        """关闭组合观察者：转发 ``close`` 给子观察者，并停止后续转发。"""
        self._dispatch("close")
        self._closed = True

    def _dispatch(self, name: str, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        """把一次调用转发给所有子观察者，返回最后一个非 None 结果。"""
        result: typing.Any = None
        if self._closed:
            return None
        for observer in self._observers:
            handler = getattr(observer, name, None)
            if handler is None or not callable(handler):
                continue
            try:
                value = handler(*args, **kwargs)
            except Exception:  # noqa: BLE001 - 旁路能力，任何异常都不应影响主流程
                logger.warning("observer %r failed on %s", type(observer).__name__, name, exc_info=True)
                continue
            if value is not None:
                result = value
        return result

    def __getattr__(self, name: str) -> typing.Callable[..., typing.Any]:
        """动态构造事件转发器。

        下划线开头的名字按常规抛 ``AttributeError``，避免污染拷贝、pickle 等
        协议探测（这些协议会通过 ``getattr`` 试探魔术方法）。
        """
        if name.startswith("_"):
            raise AttributeError(name)

        def _forward(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            return self._dispatch(name, *args, **kwargs)

        _forward.__name__ = name
        return _forward

    def __len__(self) -> int:
        return len(self._observers)

    def __repr__(self) -> str:
        names = [type(o).__name__ for o in self._observers]
        return f"CompositeRabbitObserver({names!r})"
