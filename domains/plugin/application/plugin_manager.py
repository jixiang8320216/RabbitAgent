"""插件管理器。

职责边界（当前阶段）：

- **登记**：持有「插件名 -> 插件对象」的映射，供 ``PluginOrchestrator`` 与
  ``request_context`` 查询当前启用的插件；
- **启用/禁用**：维护启用集合，支持按请求临时禁用（与
  ``plugin.application.request_context`` 的上下文变量配合使用）；
- **加载**：``load_plugins`` / ``initialize_plugins`` 负责目录发现与初始化回调，
  由 ``PluginOrchestrator.ensure_initialized`` 驱动；调用方也可以跳过这两步，
  直接用 ``register()`` 塞进已构造好的插件对象（``PluginOrchestrator`` 早期
  只做登记时就是这么用的）。

设计上默认无参构造且零 IO：``RabbitApplicationCompositionState`` 里是
``plugin_runtime.PluginManager()``，插件内容在会话初始化时填充。
"""

from __future__ import annotations

import collections.abc
import logging
import pathlib
import threading
import typing

import pydantic

import domains.plugin.application.ports as ports
import domains.plugin.domain as plugin_domain
import domains.plugin.infrastructure.plugin_loading as plugin_loading
import domains.plugin.infrastructure.execution_guard as execution_guard


logger = logging.getLogger(__name__)

__all__ = ["PluginManager"]

_PLUGIN_CONFIG_ADAPTER=pydantic.TypeAdapter(dict[str, pydantic.JsonValue])

class PluginToolconflictError(RuntimeError):

    def __init__(self, tool_name: str, owner:str, new_plugin:str) -> None:
        super().__init__(f"Tool '{tool_name}' is owned by '{owner}' and cannot alsoe be registered by plugin '{new_plugin}'")
        self.tool_name = tool_name
        self.owner = owner
        self.new_plugin = new_plugin


class PluginManager:
    """插件注册与启用状态管理（进程内，线程安全）。"""

    def __init__(self, plugins: typing.Iterable[typing.Any] | None = None) -> None:
        self._loader= plugin_loading.PluginSourceLoader()
        self._execution_guard = execution_guard.PluginExecutionGuard()
        self._lock = threading.RLock()
        self._plugin_fns: dict[str, plugin_domain.Plugin] = {}
        self._plugin_metas: dict[str, plugin_domain.PluginMeta] = {}
        self._plugin_hooks: dict[str, plugin_domain.Hooks] = {}
        self._plugin_states: dict[str, plugin_domain.PluginState] = {}
        self._registered_tools: dict[str, plugin_domain.ToolDefinition] = {}
        self._tool_owners: dict[str, str] = {}
        self._tool_groups_by_plugin: dict[str, tuple[plugin_domain.ToolGroupDefinition,...]] = {}
        self._plugin_priorities: dict[str, int] = {}
        self._active_hooks_ordered: tuple[tuple[str,plugin_domain.Hooks,int],...]=()
        self._config: collections.abc.MutableMapping[str, object] = {}
        self._directories: list[pathlib.Path] = []
        self._disabled_plugins: set[str] = ()
        self._mode_registry: ports.ModeRegistryPort|None=None

    def set_mode_registry(self, mode_registry: ports.ModeRegistryPort |None) -> None:
        with self._lock:
            self._mode_registry= mode_registry

    # --- 查询 ---

    def get(self, name: str) -> typing.Any | None:
        """按名字取插件对象；不存在返回 ``None``。"""
        with self._lock:
            return self._plugins.get(str(name))

    def names(self) -> tuple[str, ...]:
        """返回全部已登记插件名。"""
        with self._lock:
            return tuple(self._plugins.keys())

    def enabled_names(self) -> tuple[str, ...]:
        """返回已启用插件名（保持登记顺序）。"""
        with self._lock:
            return tuple(name for name in self._plugins if name in self._enabled)

    def enabled_plugins(self) -> tuple[typing.Any, ...]:
        """返回已启用插件对象。"""
        with self._lock:
            return tuple(plugin for name, plugin in self._plugins.items() if name in self._enabled)

    def is_enabled(self, name: str) -> bool:
        """指定插件是否启用。"""
        with self._lock:
            return str(name) in self._enabled

    def get_active_plugin_names(self)->list[str]:
        with self._lock:
            return [name for name,_,_ in self._active_hooks_ordered]

    # --- 状态变更 ---

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """启用/禁用插件；返回该插件是否存在。"""
        key = str(name)
        with self._lock:
            if key not in self._plugins:
                return False
            if enabled:
                self._enabled.add(key)
            else:
                self._enabled.discard(key)
            return True

    def disable(self, name: str) -> bool:
        """禁用插件。"""
        return self.set_enabled(name, False)

    def enable(self, name: str) -> bool:
        """启用插件。"""
        return self.set_enabled(name, True)

    def unregister(self, name: str) -> bool:
        """注销插件；返回是否确实删除了条目。"""
        key = str(name)
        with self._lock:
            self._enabled.discard(key)
            return self._plugins.pop(key, None) is not None

    # --- 协议 ---

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return str(name) in self._plugins

    def __len__(self) -> int:
        with self._lock:
            return len(self._plugins)

    def __repr__(self) -> str:
        return f"PluginManager(plugins={list(self.names())!r}, enabled={list(self.enabled_names())!r})"

    # --- 加载 / 初始化 ---
    #
    # 这两个方法是 ``PluginOrchestrator.ensure_initialized()`` 的入口：
    # ``load_plugins(目录, 配置, disabled=...)`` 负责发现并登记，
    # ``initialize_plugins(project_name=...)`` 负责回调插件自己的初始化并
    # 重建「活跃钩子」顺序表。分开是因为登记是纯数据操作，而初始化会回调
    # 插件代码（可能 await），期间不能一直握着锁。

    async def load_plugins(
        self,
        directories: collections.abc.Sequence[pathlib.Path],
        config: collections.abc.Mapping[str, object],
        disabled: set[str] | None = None,
    ) -> list[str]:
        with self._lock:
            self._config= dict(config)
            self._disabled_plugins = set(disabled or ())
            self._directories = list(directories)
        discovered = await self._loader.discover_plugins(directories)
        selected: dict[str, plugin_domain.PluginMeta]={}
        for meta in sorted(discovered, key=lambda  item: (-item.priority, item.name)):
            if meta.name not in self._disabled_plugins:
                selected.setdefault(meta.name,meta)
        loaded: list[str] = []
        for name,meta in selected.items():
            self._set_state(name, plugin_domain.PluginState.ERROR)
            callback = await self._load_candidate(meta)
            if callback is None:
                self._set_state(name, plugin_domain.PluginState.ERROR)
                continue
            with self._lock:
                self._plugin_fns[name]= callback
                self._plugin_metas[name]= meta
                self._plugin_priorities[name]= meta.priority
                self._plugin_states[name]= plugin_domain.PluginState.LOADED
            loaded.append(name)
        return loaded

    async def initialize_plugins(
        self,
        project_home: str,
        model_id:str ="",
        provider_id:str="" ,
    ) -> None:
        with self._lock:
            candidates = tuple(self._plugin_fns.items())
        for name,callback in candidates:
            if self.get_plugin_state(name) is plugin_domain.PluginState.ERROR:
                continue
            try:
                plugin_input = self._build_plugin_input(
                    name,
                    project_home =project_home,
                    model_id= model_id,
                    provider_id= provider_id,
                )
            except Exception:
                self._set_state(name, plugin_domain.PluginState.ERROR)
                continue
            hooks= await self._execute_candidate(name, callback, plugin_input)
            if hooks is None:
                continue
            try:
                self._active_candidate(name, hooks)
            except PluginToolconflictError:
                self._set_state(name, plugin_domain.PluginState.ERROR)
                raise
            except Exception:
                self._set_state(name, plugin_domain.PluginState.ERROR)


    async def shutdown_plugins(self, timeout_seconds: float = 60.0) -> None:
        """停用所有插件并清理已激活的钩子。

        幂等：重复调用安全。单个插件的清理失败不影响其余插件 ——
        关闭阶段不应因为一个插件出错而留下半清理状态。

        :param timeout_seconds: 单个插件清理的超时上限（保留参数以对齐调用方契约）
        """
        import asyncio

        with self._lock:
            names = [name for name, _, _ in self._active_hooks_ordered]
            self._active_hooks_ordered = ()
            self._active_hooks = {}
            for name in names:
                self._set_state(name, plugin_domain.PluginState.DISABLED)

        for name in names:
            plugin = self._plugin_fns.get(name)
            closer = getattr(plugin, "shutdown", None) or getattr(plugin, "close", None)
            if closer is None:
                continue
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=timeout_seconds)
            except Exception:
                # 清理失败只记日志 —— 关闭阶段要尽力完成全部清理
                logger.warning("plugin %s shutdown failed", name, exc_info=True)

    def has_active_plugins(self) -> bool:
        with self._lock:
            return bool(self._active_hooks)


    async def run_task_complete_hook(
        self,
        input_data: plugin_domain.TaskCompleteInput,
        output_data: plugin_domain.TaskCompleteOutput,
    ) -> tuple[typing.Any, ...]:
        return await self._run_typed_hook("on_task_complete", input_data, output_data)

    def _set_state(self, name:str, state:plugin_domain.PluginState)->None:
        with self._lock:
            self._plugin_states[name] = state

    def get_plugin_state(self, name:str)->plugin_domain.PluginState:
        with self._lock:
            return self._plugin_states.get(name, plugin_domain.PluginState.UNKNOWN)

    async def _load_candidate(self, meta: plugin_domain.PluginMeta)->plugin_domain.Plugin|None:
        if meta.path is not None:
            try:
                allowed = self._execution_guard.allows_source(meta.path, plugin_name=meta.name)
            except (OSError, SyntaxError, UnicodeError):
                return None
            if not allowed:
                return None
        module = await self._loader.import_plugin_module(meta)
        if module is None:
            return None
        return self._loader.get_plugin_function(module)

    def _build_plugin_input(
        self,
        name:str,
        *,
        project_home: str,
        model_id: str,
        provider_id: str,
    )->plugin_domain.PluginInput:
        return  plugin_domain.PluginInput(
            session_id="",
            project_home=pathlib.Path(project_home) if project_home else pathlib.Path("."),
            model_id=model_id,
            provider_id=provider_id,
            config= self._get_plugin_config(name),
        )
    def _get_plugin_config(self,name:str)-> dict[str, pydantic.JsonValue]:
        plugins= self._config.get("plugins", {})
        if not isinstance(plugins, collections.abc.Mapping):
            return {}
        plugin_configs = plugins.get("config",{})
        if not isinstance(plugin_configs, collections.abc.Mapping):
            return {}
        raw_config = plugin_configs.get(name,{})
        return _PLUGIN_CONFIG_ADAPTER.validate_python(raw_config)









