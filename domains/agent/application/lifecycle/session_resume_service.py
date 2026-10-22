from __future__ import annotations

import inspect
import typing

# 按 session_id 取回所属 RabbitApplication 实例的可调用对象。
# 用类型别名而不是引用具体类，避免 agent 包在 import 阶段的循环依赖。
SessionResumeApplicationGetter = typing.Callable[[str], typing.Any]


class SessionResumeService:
    """会话恢复服务：为 resume 流程提供「session_id -> 应用实例」与 agent 注册表加载。

    在 `RabbitApplicationCompositionState.__init__` 中构造：
    - `registery_loader` 指向 `file_agent_registry.load_agent_registry`，
      恢复流程用它重新加载该会话的 agent 注册表；
    - `set_app_getter` 注册取回应用的回调（lambda session_id: self），
      使被持久化层/恢复器持有的本服务能反向取到宿主应用。
    """

    def __init__(
        self,
        *,
        # 参数名 `registery_loader` 为历史拼写，与 rabbit_application_composition
        # 的调用保持一致；新代码可用 `registry_loader`，两者等价。
        registery_loader: typing.Callable[[], typing.Any] | None = None,
        registry_loader: typing.Callable[[], typing.Any] | None = None,
    ) -> None:
        if registry_loader is not None:
            loader = registry_loader
        else:
            loader = registery_loader
        self._registry_loader = loader
        self._app_getter: SessionResumeApplicationGetter | None = None

    def set_app_getter(self, getter: SessionResumeApplicationGetter) -> None:
        """注册按 session_id 取回宿主应用的回调。"""
        self._app_getter = getter

    def get_app(self, session_id: str) -> typing.Any | None:
        """按 session_id 取回宿主应用；未注册 getter 时返回 None。"""
        if self._app_getter is None:
            return None
        return self._app_getter(session_id)

    @property
    def app_getter(self) -> SessionResumeApplicationGetter | None:
        return self._app_getter

    def load_registry(self) -> typing.Any:
        """加载 agent 注册表；未配置 loader 时返回 None。

        loader 执行失败不向上抛异常（恢复是尽力而为的降级路径）。
        """
        if self._registry_loader is None:
            return None
        try:
            return self._registry_loader()
        except Exception:
            return None

    async def close(self) -> None:
        """释放本服务持有的资源。

        ``RabbitApplication.dispose_hook_resources`` 会调用它收尾。此前本方法
        **只有调用点、没有定义** → ``AttributeError: 'SessionResumeService'
        object has no attribute 'close'``，收尾链路被这条异常打断（外层虽然
        有 try/except 兜住，但后续的插件关闭等步骤也就跟着跳过了）。

        释放策略：调用一次 ``load_registry()`` 取回注册表实例（若它自身带
        ``close``，同步或异步都支持）并关闭；之后清空 loader 与 app getter
        引用。重复调用安全。
        """
        registry = self.load_registry()
        if registry is not None:
            closer = getattr(registry, "close", None)
            if callable(closer):
                try:
                    result = closer()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass
        self._registry_loader = None
        self._app_getter = None
