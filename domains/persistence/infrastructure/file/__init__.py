"""domains.persistence.infrastructure.file 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "DEFAULT_FILE_LOCK_SECONDS": "domains.persistence.infrastructure.file.file_lock_impl",
    "FileAppStateStore": "domains.persistence.infrastructure.file.file_app_state_store",
    "FileLockTimeoutError": "domains.persistence.infrastructure.file.file_lock_impl",
    "FileLockUnsafePathError": "domains.persistence.infrastructure.file.file_lock_impl",
    "FileSessionSnapshotStore": "domains.persistence.infrastructure.file.file_session_snapshot_store",
    "PersistencePlane": "domains.persistence.infrastructure.file.file_persisternce_factory",
    "atomic_write_json": "domains.persistence.infrastructure.file.atomic_write",
    "atomic_write_text": "domains.persistence.infrastructure.file.atomic_write",
    "create_file_persisternce_plane": "domains.persistence.infrastructure.file.file_persisternce_factory",
    "file_lock": "domains.persistence.infrastructure.file.file_lock_impl",}


# ---------------------------------------------------------------------------
# 关于 ``file_lock``：子模块刻意不叫这个名字
# ---------------------------------------------------------------------------
#
# PEP 562 的 ``__getattr__`` **只在普通属性查找失败时**才被调用。若本包存在一个
# 与导出名同名的子模块（``file/file_lock.py``），CPython 会在该子模块首次被导入时
# 把**模块对象**绑到父包的同名属性上，于是惰性导出被永久遮蔽——以包属性方式调用
# 的一方拿到的是模块而不是函数，表现为
# ``TypeError: 'module' object is not callable``（这正是启动期
# ``initialize_memory_documents`` 崩溃的原因）。
#
# 反过来若把导出名急切绑定成函数，则 ``import ...file.file_lock as m`` 这种写法又会
# 拿到函数而不是模块——``import a.b.c as x`` 走的正是父包同名属性。两种用法对同一
# 个名字的要求**互斥**，因此只能消除同名：实现文件改名为 ``file_lock_impl.py``，
# 导出名 ``file_lock`` 继续由下面的惰性映射提供。
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
