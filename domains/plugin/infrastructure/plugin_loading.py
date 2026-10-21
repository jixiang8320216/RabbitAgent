import ast
import enum
import hashlib
import inspect
import logging
import types
import typing
import pathlib
import collections
import sys
import importlib
import importlib.util
import importlib.metadata

import domains.agent.domain.value_objects.runtime_context as process_runtime_context
import pydantic

import domains.plugin.domain as plugin_domain

logger =logging.getLogger(__name__)


class SourceMetadata(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        extra = "forbid",
        frozen=True,
    )
    description:str=""
    doc:str=""
    version:str="0.0.0"
    author:str=""

class PluginSourceLoader:
    @staticmethod
    def  get_plugin_function(module:types.ModuleType|plugin_domain.Plugin)->plugin_domain.Plugin|None:
        if inspect.iscoroutinefunction(module):
            return typing.cast(plugin_domain.Plugin, module)
        namespace =vars(module)
        for attribute_name in ("plugin","Plugin"):
            callback =namespace.get(attribute_name)
            if inspect.iscoroutinefunction(callback):
                return typing.cast(plugin_domain.Plugin, callback)
        return None

    async def discover_plugins(
        self,
        directories: collections.abc.Sequence[pathlib.Path],
    )->list[plugin_domain.PluginMeta]:
        plugins:list[plugin_domain.PluginMeta] = []
        priority_base=len(directories)
        for index, directory in enumerate(directories):
            if not directory.is_dir():
                continue
            priproty=(priority_base-index)*100
            scope=self._determine_scope(directory)
            plugins.extend(self._discover_directory_plugins(directory, priproty,scope))
            plugins.extend(self._discover_file_plugins(directory, priproty, scope))
        plugins.extend(self._discover_entry_point_plugins())
        return plugins

    async def import_plugin_module(
        self,
        meta: plugin_domain.PluginMeta
    )->types.ModuleType|plugin_domain.Plugin|None:
        module_keys:list[str]=[]
        try:
            if meta.entry_point is not None:
               entry_point_value= meta.entry_point.load()
               if isinstance(entry_point_value, types.ModuleType):
                   return entry_point_value
               if inspect.iscoroutinefunction(entry_point_value):
                   return typing.cast(plugin_domain.Plugin, entry_point_value)
               return None
            if meta.path is None:
                return None
            module_key = self.module_key(meta.name)
            plugin_dir =meta.path.parent
            init_path= plugin_dir/"__init__.py"
            if init_path.is_file():
                package_module= self._load_module(
                    module_key,
                    init_path,
                    search_locations=[str(plugin_dir)],
                )
                module_keys.append(module_key)
                plugin_key=f"{module_key}.plugin"
                plugin_module= self._load_module(plugin_key, meta.path)
                module_keys.append(plugin_key)
                plugin_module.__package__= package_module.__name__
                return plugin_module
            module= self._load_module(module_key, meta.path)
            module_keys.append(module_key)
            return module
        except Exception as e:
            for module_key in module_keys:
                sys.modules.pop(module_key, None)
            return None

    @staticmethod
    def module_key(plugin_name:str)->str:
        token="".join(character if character.isalnum() else "_" for character in plugin_name)
        digest= hashlib.sha256(plugin_name.encode("utf-8")).hexdigest()[:10]
        return f"rabbit_plugin_{token}_{digest}"

    def _determine_scope(self, directory: pathlib.Path) -> typing.Literal["project","user","system"]:
        resolved= directory.resolve(strict=False)
        runtime_context = process_runtime_context.get_runtime_context_or_none(resolved)
        if runtime_context is not None:
            user_root = (runtime_context.user_data_home/"plugins").resolve(strict=False)
            if resolved == user_root or resolved.is_relative_to(user_root):
                return "user"
            if runtime_context.system_config_dir is not None:
                system_root = (runtime_context.system_config_dir/"plugins").resolve(strict=False)
                if resolved == system_root or resolved.is_relative_to(system_root):
                    return "system"
        return "project"

    def _discover_directory_plugins(
        self,
        directory: pathlib.Path,
        priority: int,
        scope: typing.Literal["project","user","system"],
    ) -> list[plugin_domain.PluginMeta]:
        plugins: list[plugin_domain.PluginMeta] = []
        try:
            children = sorted(directory.iterdir(),key=lambda item: item.name.casefold())
        except OSError as exe:
            return plugins
        for plugin_dir in children:
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                continue
            plugin_path= plugin_dir/"plugin.py"
            if  not plugin_path.is_file():
                continue
            try:
                plugins.append(self._build_directory_meta(plugin_dir, plugin_path,priority,scope))
            except (OSError, ValueError) as exe:
                 logger.warning(exe)
        return plugins

    def _discover_file_plugins(
        self,
        directory: pathlib.Path,
        priority: int,
        scope:typing.Literal["project","user","system"],
    ) -> list[plugin_domain.PluginMeta]:
        """发现 ``directory`` 下每个顶层 ``.py`` 文件形式的插件。"""
        plugins: list[plugin_domain.PluginMeta] = []
        try:
            candidates= sorted(directory.glob("*.py"),key=lambda item: item.name.casefold())
        except OSError as exe:
            return plugins
        for path in candidates:
            if path.name.startswith("_"):
                continue
            source_meta = self._extract_file_metadata(path)
            try:
               plugins.append(
                plugin_domain.PluginMeta(
                    name=path.stem,
                    path=path,
                    version= source_meta.version,
                    description= source_meta.description,
                    doc=source_meta.doc,
                    author= source_meta.author,
                    priority=priority,
                    package_scope =scope,
                )
               )
            except pydantic.ValidationError as exc:
                logger.warning(exc)
        return plugins
    @staticmethod
    def _discover_entry_point_plugins(
    ) -> list[plugin_domain.PluginMeta]:
        plugins: list[plugin_domain.PluginMeta] = []
        try:
            entry_points = importlib.metadata.entry_points(group="rabbit.plugins")
        except Exception:  # noqa: BLE001
            return plugins
        for entry_point in sorted(entry_points, key=lambda item: item.name.casefold()):
            try:
                plugins.append(
                    plugin_domain.PluginMeta(
                        name=entry_point.name,
                        entry_point=entry_point,
                        description=f"pip package plugin:{entry_points.value}",
                    )
                )
            except pydantic.ValidationError as exc:
                logger.warning(exc)
        return plugins

    @staticmethod
    def _load_module(
        module_key: str,
        path: pathlib.Path,
        *,
        search_locations: list[str] | None = None,
    ) -> types.ModuleType:
        """从文件路径加载一个模块，并以 ``module_key`` 注册到 ``sys.modules``。

        加载失败时清理已登记的 ``sys.modules`` 条目，避免半残模块污染后续重试。
        """
        spec = importlib.util.spec_from_file_location(
            module_key,
            path,
            submodule_search_locations=search_locations,
        )
        if spec is None or spec.loader is None:
            raise ImportError(
                f"cannot build import spec for plugin module {module_key!r} from {path!r}"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_key] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_key, None)
            raise
        return module

    def _build_directory_meta(
        self,
        plugin_dir: pathlib.Path,
        plugin_path: pathlib.Path,
        priority: int,
        scope: typing.Literal["project", "user", "system"],
    ) -> plugin_domain.PluginMeta:
        source_meta = self._extract_file_metadata(plugin_path)
        manifest_path = plugin_dir/"PACKAGE.toml"
        manifest = self._parse_package_manifest(manifest_path) if manifest_path.is_file() else None
        components=self._detect_package_compoents(plugin_dir)
        if manifest is not None:
            if manifest.package.name != plugin_dir.name:
                raise ValueError("Package name mismatch")
            components = plugin_domain.PackageComponents(
                skills= manifest.components.skills,
                mcp_servers = manifest.components.mcp_servers,
                prompts= manifest.components.prompts,
            )
        is_package = manifest is not None or bool(components.skills or components.mcp_servers or components.prompts)
        return plugin_domain.PluginMeta(
            name=plugin_dir.name,
            path=plugin_path,
            version=manifest.package.version if manifest is not None else source_meta.version,
            description=(manifest.package.description if manifest is not None else source_meta.description),
            doc=source_meta.doc,
            author=manifest.package.author if manifest is not None else source_meta.author,
            priority=priority,
            is_package=is_package,
            package_manifest=manifest_path if manifest is not None else None,
            package_components=components if is_package else None,
            package_scope=scope,
        )

    @staticmethod
    def _extract_file_metadata(path: pathlib.Path) -> SourceMetadata:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, ValueError, RecursionError) as exc:
            logger.debug("plugin source %s is not parseable: %s", path, exc)
            return SourceMetadata()
        values: dict[str, str] = {}
        docstring= ast.get_docstring(tree)
        if docstring :
            values["doc"] = docstring.strip()
            values["description"] = docstring.strip().split()[0].strip()
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, _ast.Assign) or not isinstance(node.value, _ast.Constant):
                continue
            if not isinstance(node.value.value, str):
                    continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "__author__",
                    "__description__",
                    "__version__",
                }:
                    values[target.id.strip("_")] = node.value.value
        return SourceMetadata.model_validate(values)

    # --- 包清单 / 包组件 ---

    @staticmethod
    def _read_package_manifest(
        plugin_dir: pathlib.Path,
    ) -> tuple[pathlib.Path | None, dict[str, object]]:
        """在插件目录里找包清单；找不到或损坏都返回 ``(None, {})``。"""
        for relative in _PACKAGE_MANIFEST_CANDIDATES:
            candidate = plugin_dir / relative
            try:
                if not candidate.is_file():
                    continue
                raw = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            try:
                data = _json.loads(raw)
            except ValueError as exc:
                logger.warning("plugin manifest %s is not valid JSON: %s", candidate, exc)
                continue
            if isinstance(data, dict):
                return candidate, data
        return None, {}

    def _package_components(
        self,
        plugin_dir: pathlib.Path,
        manifest: typing.Mapping[str, object],
    ) -> plugin_domain.PackageComponents | None:
        """解析插件包声明的组件（skills / prompts / MCP 服务器）。

        两路：清单显式声明优先；清单没写就按目录布局推断。解析失败一律给
        ``None``——组件只是描述信息，缺了它插件照样能用。
        """
        skills = _text_tuple(_manifest_field(manifest, ("skills", "skill")))
        prompts = _text_tuple(_manifest_field(manifest, ("prompts", "prompt")))
        servers = _text_tuple(_manifest_field(manifest, ("mcpServers", "mcp_servers", "mcp")))
        if not (skills or prompts or servers):
            skills = _directory_entry_names(plugin_dir / "skills")
            prompts = _directory_entry_names(plugin_dir / "prompts")
            servers = _mcp_server_names(plugin_dir)
        if not (skills or prompts or servers):
            return None
        try:
            return plugin_domain.PackageComponents(
                skills=skills,
                mcp_servers=servers,
                prompts=prompts,
            )
        except Exception as exc:  # noqa: BLE001 - 描述信息，失败就当没有
            logger.debug("cannot build package components for %s: %s", plugin_dir, exc)
            return None












