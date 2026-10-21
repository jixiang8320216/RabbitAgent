"""domains.agent.application.lifecycle 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "AgentDiagnosticContext": "domains.agent.application.lifecycle.runtime_composition",
    "AgentIdGenerator": "domains.agent.application.lifecycle.id_generator",
    "AgentInstanceManager": "domains.agent.application.lifecycle.instance_manager",
    "AgentInvocationTracker": "domains.agent.application.lifecycle.invocation_tracker",
    "AgentRecoveryService": "domains.agent.application.lifecycle.recovery_service",
    "AgentRuntimeApplicationPort": "domains.agent.application.lifecycle.runtime_composition",
    "AgentRuntimeCompositionHost": "domains.agent.application.lifecycle.runtime_composition",
    "AgentRuntimeOptions": "domains.agent.application.lifecycle.runtime_composition",
    "DefaultAgentIdGenerator": "domains.agent.application.lifecycle.id_generator",
    "PlanRuntime": "domains.agent.application.lifecycle.instance_manager",
    "RecoveryCandidate": "domains.agent.application.lifecycle.recovery_service",
    "SessionResumeApplicationGetter": "domains.agent.application.lifecycle.session_resume_service",
    "SessionResumeService": "domains.agent.application.lifecycle.session_resume_service",
    "create_react_agent": "domains.agent.application.lifecycle.runtime_composition",
    "should_attach_memory": "domains.agent.application.lifecycle.runtime_composition",}


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
