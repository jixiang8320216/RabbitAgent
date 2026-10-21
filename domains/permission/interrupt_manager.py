"""中断管理的兼容入口。

实现已下沉到 :mod:`confirmation_core.application.interrupt_coordination`——``app``
侧（``rabbit_application_composition.py``）经 ``confirmation.InterruptManager`` 访问它，
``cli`` 侧（``tui_application.py``）从 ``confirmation_core`` 导入。两处必须拿到
**同一个类**，否则中断标记会存在两份字典里、注册与读取互相看不到，因此这里只做
再导出，不保留第二份实现。
"""

from domains.permission.application.interrupt_coordination import InterruptManager

__all__ = ["InterruptManager"]
