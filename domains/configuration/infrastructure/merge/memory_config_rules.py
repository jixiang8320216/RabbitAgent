
from pathlib import Path
from collections.abc import Mapping

from domains.configuration import ConfigMapping,ConfigData
from domains.configuration.infrastructure.store.project_config_store import  read_project_config,update_project_config
from domains.configuration.infrastructure.store.user_config_store import read_user_config
from domains.configuration.infrastructure.store.user_config_store import update_user_config


def resolve_memory_enabled(config:ConfigMapping)-> bool:
    personalization = config.get("personalization")
    if isinstance(personalization,Mapping):
        memory= personalization.get("memory")
        if isinstance(memory,Mapping) and isinstance(memory.get("enabled"),bool):
            return bool(memory["enabled"])
    plugins = config.get("plugins")
    if isinstance(plugins,Mapping):
        disabled = plugins.get("disabled")
        if isinstance(disabled,list) and "memory" in disabled:
            return False
        plugin_config= config.get("config")
        if isinstance(plugin_config,Mapping):
            legacy = plugin_config.get("memory")
            if isinstance(legacy,Mapping) and isinstance(legacy.get("enabled"),bool):
                return bool(legacy["enabled"])
    return True
# 在项目级配置中清除已废弃的Memory插件相关字段（plugins.disabled中memory条目、plugins.config.memory）,不引入新的开关字段、 是Memory 配置从
# [插件式开关]演进到全局【personalization.memory.enabled】后的项目级清理入口。
def clean_project_memory_configuration(project_home: str|Path) -> bool:
    current =read_project_config(project_home)
    if not _has_legacy_memory_plugin_field(current):
        return False
    def mutate(config: ConfigData)->bool:
        before = repr(config)
        _remove_legacy_memory_plugin_fields(config)
        return repr(config)!= before
    return update_project_config(project_home, mutate)

def _remove_legacy_memory_plugin_fields(config:ConfigData)->None:
    plugins = config.get("plugins")
    if not isinstance(plugins,dict):
        return
    disabled= plugins.get("disabled")
    if isinstance(disabled,list):
        plugins["disabled"]=[item for item in disabled if item !="memory"]
        if not plugins["disabled"]:
            plugins.pop("disabled",None)
    plugin_config= config.get("config")
    if isinstance(plugin_config,dict):
        plugin_config.pop("memory",None)
        if not plugin_config:
            plugins.pop("config",None)
    if not plugins:
        config.pop("plugins",None)


def _has_legacy_memory_plugin_field(config:ConfigMapping)->bool:
    plugins= config.get("plugins")
    if not isinstance(plugins,Mapping):
        return False
    disabled= plugins.get("disabled")
    if isinstance(disabled,list)and "memory" in disabled:
        return True
    plugin_config= config.get("config")
    return isinstance(plugin_config,Mapping) and "memory" in plugin_config

def migrate_user_memory_configuration(config_path: str|Path|None=None)->bool:
    current =read_user_config(config_path)
    if not _user_migration_required(current):
        return False

    def mutate(config: ConfigData)->bool:
        before = repr(config)
        enabled = resolve_memory_enabled(config)
        personalization = _mapping_section(config,"personalization")
        memory = _mapping_section(personalization,"memory")
        memory["enabled"] = enabled
        _remove_legacy_memory_plugin_fields(config)
        return  repr(config)!= before
    return update_user_config(mutate, config_path=config_path)

def _mapping_section(config:ConfigData,key: str)->ConfigData:
    value= config.get(key)
    if isinstance(value,dict):
        return value
    section: ConfigData={}
    config[key] = section
    return section

def _user_migration_required(config: ConfigMapping)->bool:
    return _has_legacy_memory_plugin_fields(config)

def _has_legacy_memory_plugin_fields(config: ConfigMapping)->bool:
    plugins= config.get("plugins")
    if not isinstance(plugins,Mapping):
        return False
    disabled = plugins.get("disabled")
    if isinstance(disabled,list) and "memory" in disabled:
        return True
    plugin_config= config.get("config")
    return isinstance(plugin_config,Mapping) and "memory" in plugin_config



















