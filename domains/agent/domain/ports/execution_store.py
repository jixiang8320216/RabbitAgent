"""执行存储的**端口**（领域层契约）。

为什么要有这个文件
------------------

``AgentExecutionStore`` 原先定义在
``domains.agent.application.context.execution_store``，而它的消费者包含
:mod:`domains.agent.domain.aggregates.react_agent` —— 于是出现了**领域层反向依赖
应用层**的格局（``agent.domain -> agent.application``），违反「领域层不依赖上层」

协议（``typing.Protocol``）天生就该待在**最内层**：谁都可以实现它，
消费方只依赖它。所以这里把它下沉到领域层，应用层模块改为从本模块 re-export，
两边都不用改调用点即可恢复正确的依赖方向。
"""

from __future__ import annotations

import typing

import domains.agent.domain.entities.agent_context as agent_context_module
import domains.agent.domain.entities.agent_execution_paths as agent_execution_paths_module

__all__ = ["AgentExecutionPathResolver", "AgentExecutionStore"]


class AgentExecutionPathResolver(typing.Protocol):

    def paths(
            self,
            context: agent_context_module.AgentContext,
    ) -> agent_execution_paths_module.AgentExecutionPaths: ...


class AgentExecutionStore(AgentExecutionPathResolver, typing.Protocol):

    def save_state(self, context: agent_context_module.AgentContext) -> None: ...
