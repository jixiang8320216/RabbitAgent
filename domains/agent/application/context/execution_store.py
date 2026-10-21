import pydantic
import typing

from domains.agent.domain.entities import agent_context
import domains.agent.domain.entities.agent_execution_paths as agent_execution_paths


class AgentExecutionPathResolver(typing.Protocol):

    def paths(self, context: agent_context.AgentContext) -> agent_execution_paths.AgentExecutionPaths:
        ...

class AgentExecutionStore(AgentExecutionPathResolver, typing.Protocol):

    def save_state(self, context:agent_context.AgentContext)->None:...

