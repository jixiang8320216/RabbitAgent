"""domains.plugin.domain 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

from __future__ import annotations
import typing
from domains.plugin.domain.contribution import AgentSlot, AgentSlotDeclaration, ModeDeclaration, ModeDefinition


"""插件领域模型。

这里集中定义插件体系对外暴露的契约对象，供 ``app`` / ``agent`` / ``hook``
等层的调用点以类型注解方式引用，而不必反向依赖插件的具体实现。

当前阶段只包含「模式声明」（:class:`ModeDeclaration`）——它描述一个可被
``ModelRegistry`` 注册的运行模式。之所以把它建模为领域对象而非简单字典：
模式声明会在多个调用点被读写（注册、激活、快照、持久化），使用不可变对象
可以避免「谁改了这份配置」这类问题。
"""
