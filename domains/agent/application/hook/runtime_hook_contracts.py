import typing

import pydantic
import domains.agent.application.scheduling.agent_tree as agent_tree_module

class ToolHookState:
    def __init__(self)->None:
        self.execution_timestamps:dict[str,float] = {}
        self.args_cache: dict[str,str] = {}
        self.consecutive_plan_text_count=0
        self.incomplete_response_count =0

@typing.runtime_checkable
class AppModeSource(typing.Protocol):
    app_mode: str|None

@typing.runtime_checkable
class AgentTreeApplication(typing.Protocol):
    agent_tree: agent_tree_module.AgentTree

class  InvocationExecutionState(typing.Protocol):
    current_entry_agent_instance_id:str|None
    plan_agent_invocations: list[dict[str, pydantic.JsonValue]]

@typing.runtime_checkable
class  InvocationStateApplication(typing.Protocol):
    execution_context: InvocationExecutionState|None
