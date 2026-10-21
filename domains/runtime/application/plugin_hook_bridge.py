"""插件 hook 桥接。

背景：hook 的**执行**由 ``HookLifecycleCoordinator`` 负责，而 hook 的**来源**
既可能是配置文件，也可能来自插件。插件体系自己有一套启用/禁用与请求级禁用
（``plugin.application.request_context``）的逻辑，本适配器把这两侧接起来：

    HookLifecycleCoordinator -> PluginHookBridgeAdapter -> PluginManager

设计选择：

- **只持有 getter，不持有 PluginManager 实例**。插件管理器在装配到
  ``RabbitApplicationCompositionState`` 时才创建，而桥接器在
  ``runtime_bindings.set_plugin_hook_bridge(...)`` 中被注册；传 getter 可以
  避免「桥接器早于管理器存在」的时序问题，也让管理器可被替换。
- **解析失败一律降级为空**。插件未安装、已被卸载、请求级被禁用，都返回
  「没有插件 hook」，而不是抛错——hook 是旁路能力。
- 请求级禁用通过 ``get_request_disabled_plugins()`` 读取上下文变量，因此
  桥接器的判定天然跟随当前请求，无需调用方显式传递。
"""

from __future__ import annotations

import copy
import logging
import typing


from domains.plugin.application import plugin_contracts
import domains.agent.domain.value_objects.plugin_hooks as runtime_plugins_hooks


logger = logging.getLogger(__name__)

__all__ = ["PluginHookBridgeAdapter"]


class PluginHookBridgeAdapter:
    """把插件体系暴露为 hook 运行时可消费的桥接器。

    :param plugin_manager_getter: 无参可调用对象，返回插件管理器（可能为 None）。
    """

    __slots__ = ("_getter",)

    def __init__(self, plugin_manager_getter: typing.Callable[[], typing.Any] | None = None) -> None:
        self._getter = plugin_manager_getter

    # --- 插件解析 ---

    def _resolve_manager(self) -> typing.Any:
        """取出插件管理器；不可用时返回 ``None``。"""
        if self._getter is None:
            return None
        try:
            return self._getter()
        except Exception:  # noqa: BLE001 - 桥接失败降级为「无插件」
            logger.warning("plugin manager getter failed", exc_info=True)
            return None

    def _disabled_plugins(self) -> frozenset[str]:
        """读取当前请求级禁用的插件集合。"""
        try:
            from domains.plugin.application.request_context import get_request_disabled_plugins

            return frozenset(get_request_disabled_plugins() or ())
        except Exception:  # noqa: BLE001
            return frozenset()

    def enabled_plugin_names(self) -> tuple[str, ...]:
        """返回当前生效（已启用且未被请求禁用）的插件名。"""
        manager = self._resolve_manager()
        if manager is None:
            return ()
        try:
            names = tuple(manager.enabled_names())
        except AttributeError:
            return ()
        disabled = self._disabled_plugins()
        return tuple(name for name in names if name not in disabled)

    def has_plugins(self) -> bool:
        """当前是否有任何生效的插件。"""
        return bool(self.enabled_plugin_names())

    def plugin_hooks_for_event(self, event: typing.Any) -> tuple[typing.Any, ...]:
        """收集指定事件下由插件贡献的 hook。

        插件可以两项方式贡献 hook：暴露 ``hooks_for_event(event)``，或暴露
        ``plugin_hooks`` 映射。两者都不存在时该插件被跳过。
        """
        manager = self._resolve_manager()
        if manager is None:
            return ()
        event_key = str(getattr(event, "value", event))
        collected: list[typing.Any] = []
        disabled = self._disabled_plugins()
        for name, plugin in _iter_enabled_plugins(manager):
            if name in disabled:
                continue
            collected.extend(_collect_plugin_event_hooks(plugin, event_key))
        return tuple(collected)

    def __repr__(self) -> str:
        return f"PluginHookBridgeAdapter(plugins={list(self.enabled_plugin_names())!r})"

    # --- 聊天级插件 hook ---

    async def run_chat_headers_hook(
        self,
        input_data: runtime_plugins_hooks.ChatHeadersInput,
        output_data: runtime_plugins_hooks.ChatHeadersOutput,
    ) -> None:
        manager= self._resolve_manager()
        if manager is None:
            return
        plugin_input = plugin_contracts.ChatHeaderInput(headers=dict(input_data.headers))
        plugin_output= plugin_contracts.ChatHeaderOutput(headers=dict(output_data.headers))
        await manager.run_chat_headers_hook(plugin_input, plugin_output)
        validated_ouput= runtime_plugins_hooks.ChatHeadersOutput(headers=plugin_output.headers)
        output_data.headers= validated_ouput.headers

    async def run_chat_params_hook(
        self,
        input_data: runtime_plugins_hooks.ChatHeadersInput,
        output_data: runtime_plugins_hooks.ChatHeadersOutput,
    ) -> None:
        manager= self._resolve_manager()
        if manager is None:
            return
        plugin_input = plugin_contracts.ChatHeaderInput(
            temperature=input_data.temperature,
            top_p= input_data.top_p,
            top_k= input_data.top_k,
            max_tokens= input_data.max_tokens,
            options = copy.deepcopy(input_data.options)
        )
        plugin_output= plugin_contracts.ChatHeaderOutput(
            temperature=output_data.temperature,
            top_p=output_data.top_p,
            top_k=output_data.top_k,
            max_tokens=output_data.max_tokens,
            options=copy.deepcopy(output_data.options)
        )
        await manager.run_chat_params_hook(plugin_input, plugin_output)
        validated_ouput= runtime_plugins_hooks.ChatParamsOutput(
            temperature=plugin_output.temperature,
            top_p=plugin_output.top_p,
            top_k=plugin_output.top_k,
            max_tokens=plugin_output.max_tokens,
            options=copy.deepcopy(plugin_output.options)
        )
        output_data.temperature= validated_ouput.temperature
        output_data.top_p= validated_ouput.top_p
        output_data.top_k= validated_ouput.top_k
        output_data.max_tokens= validated_ouput.max_tokens
        output_data.options= validated_ouput.options

    async def _run_plugin_event_hooks(
        self,
        event: str,
        hook_input: typing.Any,
        hook_output: typing.Any,
    ) -> None:
        """通用：收集指定事件的插件 hook 并依次运行（就地修改 ``hook_output``）。

        单个 hook 失败仅记录告警、不向上抛——插件 hook 是旁路增强，不应阻断
        正常的聊天请求。hook 既可以是同步也可以是异步可调用对象。
        """
        hooks = self.plugin_hooks_for_event(event)
        for hook in hooks:
            if not callable(hook):
                continue
            try:
                result = hook(hook_input, hook_output)
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001 - 旁路 hook 失败不应阻断请求
                logger.warning(
                    "%s plugin hook %r failed",
                    event,
                    getattr(hook, "__qualname__", hook),
                    exc_info=True,
                )


def _iter_enabled_plugins(manager: typing.Any) -> typing.Iterator[tuple[str, typing.Any]]:
    """遍历插件管理器中已启用的插件，产出 ``(name, plugin)``。"""
    enabled_names = getattr(manager, "enabled_names", None)
    get_plugin = getattr(manager, "get", None)
    if not callable(enabled_names) or not callable(get_plugin):
        return
    try:
        names = enabled_names()
    except Exception:  # noqa: BLE001
        return
    for name in names:
        plugin = get_plugin(name)
        if plugin is not None:
            yield str(name), plugin


def _collect_plugin_event_hooks(plugin: typing.Any, event_key: str) -> list[typing.Any]:
    """从单个插件中取出指定事件的 hook 列表。"""
    provider = getattr(plugin, "hooks_for_event", None)
    if callable(provider):
        try:
            return list(provider(event_key) or ())
        except Exception:  # noqa: BLE001
            logger.warning("plugin %r failed to provide hooks", type(plugin).__name__, exc_info=True)
            return []
    mapping = getattr(plugin, "plugin_hooks", None)
    if isinstance(mapping, dict):
        return list(mapping.get(event_key, ()) or ())
    return []
