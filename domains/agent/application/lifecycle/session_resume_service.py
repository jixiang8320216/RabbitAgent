from __future__ import annotations

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
