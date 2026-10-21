"""domains.session.domain 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "FlatSourceKind": "domains.session.domain.migration",
    "JsonObject": "domains.session.domain.json_document",
    "MigrationFileRecord": "domains.session.domain.migration",
    "MigrationKind": "domains.session.domain.migration",
    "MigrationManifest": "domains.session.domain.migration",
    "MigrationStatus": "domains.session.domain.migration",
    "SourceFile": "domains.session.domain.migration",
    "parse_json_object": "domains.session.domain.json_document",
    "parse_json_object_json": "domains.session.domain.json_document",}


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
