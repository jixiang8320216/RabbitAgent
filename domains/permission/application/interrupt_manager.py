"""中断管理的兼容入口。

实现位于 :mod:`confirmation_core.application.interrupt_coordination`。这里只做
再导出：``InterruptManager`` 的状态是**类级**的，一旦出现两份实现，注册与读取
就会落到不同的字典上，中断与取消会静默失效。
"""

from domains.permission.application.interrupt_coordination import InterruptManager

__all__ = ["InterruptManager"]
