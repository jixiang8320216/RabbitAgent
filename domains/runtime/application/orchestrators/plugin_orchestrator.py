import typing
import pathlib
import logging

import pydantic

from domains.configuration.domain.model import app_config as application_configuration
import domains.configuration.domain.protocol.config_protocol as config_protocol
import domains.plugin.application.plugin_manager as plugin_runtime_module
import domains.agent.application.registry.model_registry as mode_registry_module
import domains.configuration.domain.protocol.project_layout as project_layout_module
import domains.configuration.domain.model.llm_config as llm_config_module

JsonObject = llm_config_module.JsonObject

logger = logging.getLogger(__name__)


class PlginLoadPlan(pydantic.BaseModel):
    model_config= pydantic.ConfigDict(frozen=True)
    directories: tuple[pathlib.Path,...]
    configuration: JsonObject
    disabled_plugins: set[str]=pydantic.Field(default_factory=set)


class PluginSettings(pydantic.BaseModel):
    """一份配置里plugins 段的归一化视图。"""

    model_config= pydantic.ConfigDict(frozen=True)
    disabled: frozenset[str] = frozenset()
    config: JsonObject = pydantic.Field(default_factory=dict)

    def merged_with(self, other: "PluginSettings") -> "PluginSettings":
        """项目级覆盖运行时级：``config`` 逐键覆盖，``disabled`` 取并集。"""
        merged_config = dict(self.config)
        merged_config.update(other.config)
        return PluginSettings(
            disabled=self.disabled | other.disabled,
            config=merged_config,
        )


def _plugin_settings(configuration: JsonObject, *, source: str) -> PluginSettings:
    """从一份配置字典里取出 ``plugins`` 段。

    ``plugins`` 可能不存在、不是 dict、``disabled`` 不是 list——这些都是合法的
    "用户没配"，统一降级成空设置，不抛异常。
    """
    plugins = configuration.get("plugins") if isinstance(configuration, dict) else None
    if not isinstance(plugins, dict):
        logger.debug("no plugins section in %s configuration", source)
        return PluginSettings()

    disabled_raw = plugins.get("disabled")
    disabled = (
        frozenset(str(item) for item in disabled_raw)
        if isinstance(disabled_raw, list)
        else frozenset()
    )
    per_plugin = plugins.get("config")
    return PluginSettings(
        disabled=disabled,
        config=dict(per_plugin) if isinstance(per_plugin, dict) else {},
    )


def _global_scoped_disabled_plugins(disabled: frozenset[str]) -> set[str]:
    """全局作用域的禁用列表：原样透传。

    全局配置里的 ``plugins.disabled`` 不带作用域前缀，直接生效。
    """
    return set(disabled)


def _project_scoped_disabled_plugins(disabled: frozenset[str]) -> set[str]:
    """项目作用域的禁用列表。

    项目配置支持 ``project:<name>`` 前缀只禁用某个插件在项目内的加载；
    带前缀的条目去掉前缀后加入集合，不带的按全局禁用处理。
    """
    resolved: set[str] = set()
    for item in disabled:
        name = item.split(":", 1)[1].strip() if item.startswith("project:") else item
        if name:
            resolved.add(name)
    return resolved


def _merge_project_plugin_settings(
    configuration: JsonObject,
    project_plugins: JsonObject,
) -> None:
    """把项目级plugins 段合并进运行时配置（原地修改）。

    项目级只做「覆盖」：``config`` 逐键覆盖，``disabled`` 取并集——
    这样用户在项目里禁用一个插件不会意外把全局禁用的其它插件重新打开。
    """
    project_settings = _plugin_settings({"plugins": project_plugins}, source="project")
    plugins = configuration.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        plugins = {}
        configuration["plugins"] = plugins

    disabled = plugins.get("disabled")
    merged_disabled = list(disabled) if isinstance(disabled, list) else []
    for name in sorted(project_settings.disabled):
        if name not in merged_disabled:
            merged_disabled.append(name)
    if merged_disabled:
        plugins["disabled"] = merged_disabled

    if project_settings.config:
        per_plugin = plugins.get("config")
        if not isinstance(per_plugin, dict):
            per_plugin = {}
            plugins["config"] = per_plugin
        per_plugin.update(project_settings.config)


def _plugin_directories(
    host: PluginOrchestrationHost,
    layout: project_layout_module.ProjectLayout,
) -> tuple[pathlib.Path, ...]:
    """插件扫描目录：项目 ``.rabbit/plugins`` 优先，再加用户级目录。"""
    directories: list[pathlib.Path] = [layout.rabbit_path("plugins")]

    project_home = getattr(host, "user_project_home", None)
    if project_home:
        directories.append(
            pathlib.Path(project_home).expanduser().resolve(strict=False) / ".rabbit" / "plugins"
        )

    try:
        user_config_dir = _user_config_dir()
    except Exception:  # noqa: BLE001 - 用户目录不可解析时只扫项目目录
        user_config_dir = None
    if user_config_dir is not None:
        directories.append(pathlib.Path(user_config_dir) / "plugins")

    unique: list[pathlib.Path] = []
    for directory in directories:
        if directory not in unique:
            unique.append(directory)
    return tuple(unique)


def _user_config_dir() -> pathlib.Path | None:
    try:
        import domains.configuration as runtime_configuration_module

        return pathlib.Path(runtime_configuration_module.get_user_config_dir())
    except Exception:  # noqa: BLE001 - 运行期未装配配置目录时降级
        return None


def _runtime_configuration(host: PluginOrchestrationHost) -> JsonObject:
    """读用户级运行时配置（``<user_config_dir>/config.json``）。

    这是 ``compose_plugin_load_plan`` 的第一份数据来源（项目级配置随后叠加）。
    ``configuration`` 包只导出 ``parse_config_data``（没有
    ``read_user_config``），所以这里读文件后自己解析。

    读不到 / 解析失败就用空 dict——插件加载不是主流程的硬依赖，不应因为配置
    文件缺失或损坏就让整个应用起不来。
    """
    del host  # 当前实现只依赖全局配置目录，host 保留给后续按 host 定制的解析
    try:
        import domains.configuration as runtime_configuration_module

        config_file = runtime_configuration_module.get_user_config_dir() / "config.json"
        if not config_file.is_file():
            logger.debug("no user configuration at %s", config_file)
            return {}
        parsed = runtime_configuration_module.parse_config_data(
            config_file.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - 配置不可用时退化为空配置
        logger.warning("runtime plugin configuration unavailable: %s", exc)
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def load_project_plugin_configuration(
    config_file: pathlib.Path,
) -> JsonObject | None:
    """读项目级 ``.rabbit/config.json`` 的 plugins 段。

    文件不存在 / 不是 dict / 没有 plugins 段时都返回 ``None``，
    调用方据此跳过项目级合并。
    """
    try:
        if not config_file.is_file():
            return None
        import domains.configuration as runtime_configuration_module

        parsed = runtime_configuration_module.parse_config_data(
            config_file.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - 项目配置损坏不应阻断插件加载
        logger.warning("project plugin configuration unreadable (%s): %s", config_file, exc)
        return None
    if not isinstance(parsed, dict):
        return None
    plugins = parsed.get("plugins")
    return plugins if isinstance(plugins, dict) else None

def compose_plugin_load_plan(
    host: PluginOrchestrationHost,
    layout: project_layout_module.ProjectLayout,
)->PlginLoadPlan:
    configuration = _runtime_configuration(host)
    runtime_settings = _plugin_settings(configuration, source="runtime")
    disabled_plugins = _global_scoped_disabled_plugins(runtime_settings.disabled)

    project_configuration = load_project_plugin_configuration(layout.config_file)
    if project_configuration is not None:
        _merge_project_plugin_settings(configuration, project_configuration.plugins)
        disabled_plugins.update(_project_scoped_disabled_plugins(project_configuration.plugins.disabled))

    runtime_disabled= host.config.disabled_plugins or []
    disabled_plugins.update(runtime_disabled)
    if runtime_disabled:
        logger.warning("Appconfig.disabled ")
    return PlginLoadPlan(
        directories = _plugin_directories(host,layout),
        configuration = configuration,
        disabled_plugins = disabled_plugins,
    )

class PluginOrchestrationHost(typing.Protocol):
    config: application_configuration.AppConfig
    config_manager: config_protocol.ConfigManagerProtocol|None
    plugin_manager: plugin_runtime_module.PluginManager
    mode_registry: mode_registry_module.ModeRegistry
    user_project_name: pathlib.Path

class PluginOrchestrator:

    def __init__(self, app: PluginOrchestrationHost)->None:
        self.app = app
        self._initialized:bool = False

    async def ensure_initialized(self)->None:
        if self._initialized:
            return
        self.app.plugin_manager.set_mode_registry(self.app.mode_registry)
        await self.load_and_initialize_plugins()
        self._initialized = True

    async def load_and_initialize_plugins(self)->None:
        try:
            project_layout= project_layout_module.ProjectLayout(project_home=self.app.user_project_home)
            load_plan = compose_plugin_load_plan(self.app,project_layout)
            await self.app.plugin_manager.load_plugins(
                load_plan.directories,
            load_plan.configuration,
            disabled= load_plan.disabled_plugins,)
            await self.app.plugin_manager.initialize_plugins(
                # ``PluginManager.initialize_plugins`` 的形参是 ``project_home``
                # （项目根目录），不是 ``project_name``（项目名）。原代码传
                # ``project_name=str(...)`` 会抛
                # "initialize_plugins() got an unexpected keyword argument 'project_name'"，
                # 被下面这个宽 except 吞掉，导致所有插件都不会被初始化。
                project_home=str(getattr(self.app, "user_project_home", "") or "")
            )
            if load_plan.disabled_plugins:
                logger.info("loaded with disabled plugins")
        except Exception as e:
            logger.error(e)


