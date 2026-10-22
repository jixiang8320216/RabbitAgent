"""执行存储的端口（**已下沉到领域层**）。

这里保留模块名只是为了不破坏既有导入路径：定义本体已经下沉到领域层的
``domains.agent.domain.ports.execution_store``。

为什么要移：原定义在应用层，而它的消费者之一
``domains.agent.domain.aggregates.react_agent`` 是**领域层**对象，于是形成
「领域依赖应用」的反向依赖。端口属于最内层，应用层 re-export 即可，
调用点一行都不用改。
"""

from __future__ import annotations

from domains.agent.domain.ports.execution_store import (
    AgentExecutionPathResolver,
    AgentExecutionStore,
)

__all__ = ["AgentExecutionPathResolver", "AgentExecutionStore"]
