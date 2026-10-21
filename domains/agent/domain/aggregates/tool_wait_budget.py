"""工具等待预算的作用域。

``ReactReplyOrchestrator.run`` 在整轮 ReAct 循环外面套一层
``tool_wait_bugdget_scope()``：工具（尤其是 MCP 工具）的等待不能被算成「模型在
思考」，两者要有各自的预算，否则一次慢工具会把整轮的超时额度吃光。

当前实现是**占位版**：只负责把上下文管理器的契约立起来（进入/退出对称、异常安全），
真正的额度记账依赖:

- 预算来源：``runtime.service.execution_context`` 里的运行期配置，或 AgentSlot
  声明里的工具超时项——目前两者都还没有落地；
- 消费方：工具执行器在每次 await 前 ``consume(seconds)``。

占位版不做任何限制，因此不会改变现有行为；等上面的两块补齐后，把它替换成真实
的记账即可，调用方（``run``）无需改动。

命名说明：``tool_wait_bugdget_scope`` 沿用调用点既有的拼写（``bugdget``），
同时导出正确拼写的 ``tool_wait_budget_scope`` 作为别名，两条名字都能用。
"""

from __future__ import annotations

import contextlib
import typing

__all__ = ["tool_wait_bugdget_scope", "tool_wait_budget_scope", "remaining_tool_wait_budget"]


@contextlib.contextmanager
def tool_wait_bugdget_scope() -> typing.Iterator[None]:
    """进入一轮 ReAct 的工具等待预算作用域。

    占位实现：什么都不做，但保证异常传出时作用域仍能正常退出。
    """
    yield None


#: 正确拼写的别名。
tool_wait_budget_scope = tool_wait_bugdget_scope


def remaining_tool_wait_budget() -> float | None:
    """本轮剩余的工具等待预算（秒）；未启用预算时返回 ``None``。"""
    return None
