"""domains.configuration.domain.model 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AppConfig": "domains.configuration.domain.model.app_config",
    "AppMode": "domains.configuration.domain.model.app_mode",
    "BUILTIN_APP_MODES": "domains.configuration.domain.model.app_mode",
    "ConfigData": "domains.configuration.domain.model.config_data",
    "ConfigMapping": "domains.configuration.domain.model.config_data",
    "JsonObject": "domains.configuration.domain.model.config_data",
    "LLMConfig": "domains.configuration.domain.model.llm_config",
    "LLMConfigProvider": "domains.configuration.domain.model.llm_config",
    "ModelInfo": "domains.configuration.domain.model.app_config",
    "OpenAIAPIProtocol": "domains.configuration.domain.model.llm_profiles",
    "RuntimeConfigModel": "domains.configuration.domain.model.config_data",
    "RuntimeLLMProfile": "domains.configuration.domain.model.llm_profiles",
    "RuntimeLLMProfiled": "domains.configuration.domain.model.llm_profiles",
    "RuntimeMCPServerConfig": "domains.configuration.domain.model.mcp_server_config",
    "RuntimeMCPTransport": "domains.configuration.domain.model.mcp_server_config",
    "RuntimeModelProfileOverride": "domains.configuration.domain.model.llm_profiles",
    "SessionMode": "domains.configuration.domain.model.app_config",
    "clean_llm_config_for_save": "domains.configuration.domain.model.llm_config",
    "normalize_llm_config_for_read": "domains.configuration.domain.model.llm_config",
    "parse_config_data": "domains.configuration.domain.model.config_data",}


def __getattr__(name):
    import importlib

    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
