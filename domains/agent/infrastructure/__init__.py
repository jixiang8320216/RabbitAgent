"""domains.agent.infrastructure 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "FileAgentExecutionStore": "domains.agent.infrastructure.file_agent_execution_store",
    "FileAgentSpawnMainfestRepository": "domains.agent.infrastructure.file_agent_spawn_mainfest_repository",
    "SECTION_SEPARATOR": "domains.agent.infrastructure.static_environment_context",
    "append_prompt_sections": "domains.agent.infrastructure.static_environment_context",
    "append_static_agent_context": "domains.agent.infrastructure.static_environment_context",
    "await_agent_result": "domains.agent.infrastructure.management_tools",
    "cancel_agent": "domains.agent.infrastructure.management_tools",
    "check_agent_status": "domains.agent.infrastructure.management_tools",
    "create_file_agent_execution_store": "domains.agent.infrastructure.file_agent_execution_store",
    "load_agent_registry": "domains.agent.infrastructure.file_agent_registry",}


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
