"""``AgentCallState`` 的兼容重导出层。

历史上部分调用方按 ``agent.service.agent_call_state`` 这个路径引用
``AgentCallState``；实现迁移到 ``agent.model.agent_call_state`` 后，这里保留一个
薄重导出，避免旧导入路径拿到空模块而抛
``AttributeError: module 'agent.service.agent_call_state' has no attribute 'AgentCallState'``。

真正的实现见 :mod:`agent.model.agent_call_state`。
"""

from domains.agent.domain.entities.agent_call_state import AgentCallState

__all__ = ["AgentCallState"]
