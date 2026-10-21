"""Agent 树节点。

``AgentTree`` 用它记录每个 agent 的身份与父子关系；``scope`` 存取消作用域
（``runtime.service.cancellation.CancelScope``），``runtime`` 存 agent 本体。

字段全部给默认值：树是按「先建节点、后补信息」的方式增长的，装配期能拿到什么
就填什么。
"""

from __future__ import annotations

import typing

import pydantic


class AgentNode(pydantic.BaseModel):
    """agent 树上的一个节点。

    :param agent_id: 节点标识（通常等于 agent 名）。
    :param name: 展示名；缺省时与 ``agent_id`` 相同。
    :param parent_id: 父节点标识；根节点为 ``None``。
    :param scope: 取消作用域，``bind_scope`` 写入。
    :param runtime: agent 运行时本体（任意对象）。
    """

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True, extra="allow")

    agent_id: str = ""
    name: str = ""
    parent_id: str | None = None
    scope: typing.Any = None
    runtime: typing.Any = None

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"AgentNode(agent_id={self.agent_id!r}, parent_id={self.parent_id!r})"
