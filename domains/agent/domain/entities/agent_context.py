"""Agent 上下文与状态模型。

这一层是「agent 在注册表里是什么身份、现在处于什么状态」的载体：

- :class:`AgentStatus` —— 生命周期状态枚举；
- :class:`AgentContext` —— 单个 agent 的身份卡（``agent_id`` / 当前状态 / 归属注册表）；
- :class:`AgentRegistry` —— 极小的内存注册表，负责状态广播。

设计约束（**务必保留**）：

``runtime/service/react_agent.py`` 在**异常分支**里访问
``AgentStatus.FAILED`` / ``AgentStatus.INTERRUPTED``（见
``_run_registered_reply`` / ``_mark_interrupted``）。也就是说这些名字必须在
**模块属性层面**存在——一旦缺失，抛的是 ``AttributeError``，会把真正导致失败的
原始异常替换掉，问题现场就永久丢失了。同理，``AgentContext`` 必须真的带有
``.registry`` / ``.agent_id`` / ``.status`` 三个属性。

另外 ``react_agent.py:59`` 的形参注解写的是 ``AGgentContext``（多了一个 G）。
Python 3.14 的 PEP 649 让注解延迟求值，所以导入期不会炸；但一旦有人调用
``typing.get_type_hints`` 就会 ``AttributeError``。这里显式保留同名别名兜住。
"""

from __future__ import annotations

import enum
import threading
import typing

import pydantic

__all__ = [
    "AgentStatus",
    "AgentContext",
    "AgentRuntime",
]

class AgentWaitMode(str, enum.Enum):

    SYNC = "sync"
    ASYNC = "async"

class AgentStatus(str, enum.Enum):
    """Agent 生命周期状态。

    继承 ``str`` 是为了让状态值可以直接进日志、进 JSON、和字符串比较
    （``context.status == "completed"``）——调用方两种写法都在用。
    """
    PENDING = "pending"
    RUNNING = "running"
    WAITING_TOOL = "waiting"
    SYNC_WAITING_CHILD= "sync_waiting_child"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    def __str__(self) -> str:  # pragma: no cover - 便于日志可读
        return self.value

class AgentInterruptionReason(str, enum.Enum):
    USER_STOP_AGENT="user_stop_agent"
    USER_STOP_ALL_AGENTS="user_stop_all_agents"

class AgentContextRecord(typing.Protocol):
    """按 ``agent_id`` 记录 agent 上下文并广播状态变化。

    只实现被真实调用到的两个动作：``register`` 与 ``update_status``。
    状态变更同步给可选的 ``on_status`` 观察者（observer / 遥测都可以挂上来）；
    观察者抛异常一律吞掉——上报失败不应该打断一轮对话。
    """
    agent_id:str
    status:AgentStatus
    wait_mode:AgentWaitMode
    current_tool:str|None
    interruption_reason:AgentInterruptionReason|None


@typing.runtime_checkable
class AgentRegistryPort(typing.Protocol):

    def register(self, context: AgentContextRecord) -> None:
        ...

    def update_status(self, agent_id: object, status: AgentStatus, current_tool:str|None=None) -> None:...

    def update_wait_mode(self, agent_id:str, wait_mode: AgentWaitMode) -> bool:...

    def get_children(self, parent_id:str)->abc.Sequence[AgentContext]:
        ...

    def get(self, agent_id: str) -> AgentContext | None:...

class AgentContext(pydantic.BaseModel):
    """单个 agent 的身份卡。

    字段全部可选：装配期能拿到什么就填什么，拿不到就留空——``ReActAgent``
    对 ``agent_context`` 是「有就上报、没有就静默降级」的用法。
    """

    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        strict=True,
        validate_assignment=True,
    )

    agent_id: str =pydantic.Field(min_length=1)
    session_id:str = pydantic.Field(min_length=1)
    parent_agent_id:str|None = None
    status: AgentStatus = AgentStatus.PENDING
    wait_mode:AgentWaitMode = AgentWaitMode.ASYNC
    iteration:int= pydantic.Field(default=0, ge=0)
    current_tool: str|None = None
    started_at:str|None = None
    last_heartbeat: str|None = None
    task_description:str|None = None
    result_text: str|None = None
    interruption_reason: AgentInterruptionReason |None = None
    registry:AgentRegistryPort|None = pydantic.Field(default=None, exclude=True, repr=False)

