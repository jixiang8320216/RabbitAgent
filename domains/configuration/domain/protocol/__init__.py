"""domains.configuration.domain.protocol 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "ConfigManagerProtocol": "domains.configuration.domain.protocol.config_protocol",
    "ProjectLayout": "domains.configuration.domain.protocol.project_layout_impl",
    "project_layout": "domains.configuration.domain.protocol.project_layout_impl",}


# ---------------------------------------------------------------------------
# 关于 ``project_layout``：子模块刻意不叫这个名字
# ---------------------------------------------------------------------------
#
# 与 ``domains/persistence/infrastructure/file`` 里的 ``file_lock`` 属同一类冲突：
# 导出名与子模块名重名时，CPython 的子模块绑定会遮蔽 PEP 562 惰性导出；而若把导出
# 名急切绑定成函数，``import ...protocol.project_layout as m`` 又会拿到函数而非模块。
# 两种用法对同一个名字的要求互斥，只能消除同名——实现文件改名为
# ``project_layout_impl.py``，导出名继续由下面的惰性映射提供。
#
# 这类冲突已纳入 ``tools/check_architecture.py`` 的规则 4，新增即拦截。


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
