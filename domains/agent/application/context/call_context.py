import contextvars

import domains.agent.domain.entities.agent_call_state as agent_call_state

_agent_call_state: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agent_call_state",default=None,
)

def get_agent_call_state() -> agent_call_state.AgentCallState|None:
    return _agent_call_state.get()

def set_agent_call_state(state: agent_call_state.AgentCallState|None)->None:
    _agent_call_state.set(state)

def clear_agent_call_state()->None:
    _agent_call_state.clear()