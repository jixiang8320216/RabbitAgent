"""Agent 树（``AgentTree``）。

维护「哪些 agent 存在、谁是谁的根、谁是谁的子」这层关系。装配期由
``RabbitApplication.root_agent`` 的 setter 调 ``register_root`` 登记根 agent；
子 agent 由 spawn 流程调 ``register_child`` 登记。

取消作用域（``CancelScope``）通过 ``bind_scope`` 挂到节点上，中断逻辑靠它取消
某一棵子树。
"""

from __future__ import annotations
import datetime as datetime_module
import typing

import domains.agent.domain.entities.agent_tree_node as agent_tree_node
import domains.agent.domain.aggregates.cancellation as cancel_scope
import domains.agent.domain.aggregates.react_agent as agent_module

def _utc_now()->datetime_module.datetime:
    return datetime_module.datetime.now(datetime_module.UTC)


class AgentTree:


    def __init__(self,)->None:
        self._nodes:dict[str,agent_tree_node.AgentNode]={}
        self._root_id:str|None=None
        self._root_ids: list[str]=[]
        self._is_forest =False
        self._children_map: dict[str,set[str]]={}

    # --- 登记 ------------------------------------------------------------

    def register_root(self, agent: agent_module.ReActAgent, *, scope:cancel_scope.CancelScope|None=None)->str:
        """登记（或复用）一个根 agent 节点。

        幂等：同一个 agent 重复登记只更新节点上的 runtime，不会多出一棵树。
        登记第二个根时整棵树变成森林（``_is_forest``）。
        """
        agent_id =agent.name
        candidate = agent_tree_node.AgentNode(
            instance=agent,
            agent_id=agent_id,
            parent_id=None,
            create_at=_utc_now(),
            scope=scope,
        )
        if self._is_forest or (self._root_id is not None and self._root_id!=agent_id):
            self._clear()
        existing=self._nodes.get(agent_id)
        if existing is None:
            self._nodes[agent_id]=candidate
        else:
            if existing.parent_id is not None:
                raise ValueError("agent is already registered")
            existing.instance=agent
            if scope is not None:
                existing.scope=scope
        self._root_id = agent_id
        self._root_ids.clear()
        self._is_forest = False
        self._children_map.setdefault(agent_id, set())
        return agent_id

    # --- 查询 ------------------------------------------------------------

    def get(self, agent_id: str)->agent_tree_node.AgentNode|None:
        return self._nodes.get(agent_id)

    def children_of(self, agent_id: str)->list[str]:
        return sorted(self._children_map.get(agent_id, ()))

    @property
    def root_id(self)->str|None:
        return self._root_id

    @property
    def root_ids(self)->list[str]:
        return list(self._root_ids)

    @property
    def is_forest(self)->bool:
        return self._is_forest

    def __contains__(self, agent_id: object)->bool:
        return str(agent_id or "") in self._nodes

    def __len__(self)->int:
        return len(self._nodes)

    # --- 作用域 ----------------------------------------------------------

    def bind_scope(self, agent_id:str, scope: cancel_scope.CancelScope)->None:
        node=self._nodes.get(agent_id)
        if node is None:
            raise ValueError(f"agent '{agent_id}' not exist'")
        node.scope=scope

    # --- 内部 ------------------------------------------------------------

    @staticmethod
    def _agent_id_of(agent: typing.Any)->str:
        agent_id = getattr(agent, "name", None) or getattr(agent, "agent_id", None)
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("AgentTree: agent must expose a non-empty 'name'")
        return agent_id

    def __repr__(self)->str:  # pragma: no cover - 调试用
        return f"AgentTree(size={len(self._nodes)}, roots={self._root_ids})"
