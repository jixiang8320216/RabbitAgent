import contextvars
import typing
import logging

import domains.plugin.application.ports as ports


logger  = logging.getLogger(__name__)
_current_plugin_runtime: contextvars.ContextVar[ports.PluginRuntimePort | None] =  contextvars.ContextVar(
    "_current_plugin_runtime",
     default=None,
)

_request_disabled_plugins: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar(
    "request_disabled_plugins",
    default=None,
)


def set_request_disbled_plugins(plugins: typing.Iterable[str]) -> None:
    """
    设置当前请求禁用的插件名集合。

    注意：方法名 ``disbled`` 沿用项目既定 typo（应为 ``disabled``），
    与 ``request_context.py`` 调用点保持一致。
    """
    _request_disabled_plugins.set(frozenset(plugins))


def get_request_disabled_plugins() -> frozenset[str] | None:
    """获取当前请求禁用的插件集合；未设置返回 None。"""
    return _request_disabled_plugins.get()


# --- 插件管理器的进程级槽位 -------------------------------------------------

_plugin_manager_getter: typing.Callable[[], typing.Any] | None = None


def set_plugin_manager_getter(getter: typing.Callable[[], typing.Any] | None) -> None:
    """安装「取插件管理器」的回调。

    存回调而非实例，是因为插件管理器在应用装配阶段才被创建，而本模块可能在
    更早的导入链上被装载（``app.application.rabbit_application_composition``
    直接 import 本模块并传入 ``get_plugin_manager``）。
    """
    global _plugin_manager_getter
    _plugin_manager_getter = getter

def set_plugin_manager(runtime: ports.PluginRuntimePort|None) -> None:
    _current_plugin_runtime.set(runtime)
    if runtime is not None:
        logger.debug("bOUND Plugin runtime to curretn request")


def get_plugin_manager() -> typing.Any | None:
    """取出当前插件管理器；未安装 getter 或取用失败时返回 ``None``。

    返回 ``None`` 是受支持的正常情形：插件 hook 桥接器会在这种情况下退化为
    「不执行任何插件 hook」，而不是抛错打断主流程。
    """
    getter = _plugin_manager_getter
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - 槽位读取不应打断调用方
        return None


# --- 当前正在装配的 agent 槽位 ---------------------------------------------

_current_slot: contextvars.ContextVar[typing.Any | None] = contextvars.ContextVar(
    "_current_slot",
    default=None,
)


def set_current_slot(slot: typing.Any | None) -> None:
    """记录当前正在装配的 agent 槽位（``AgentSlotDeclaration``）。

    插件据此判断「这次构建属于哪个槽位」，例如按槽位的工具白名单裁剪可见工具。
    槽位未设置时读到的就是 ``None``，插件应当按「无限制」处理，而不是报错。
    """
    _current_slot.set(slot)


def get_current_slot() -> typing.Any | None:
    """取出当前正在装配的 agent 槽位；未设置返回 ``None``。"""
    return _current_slot.get()
