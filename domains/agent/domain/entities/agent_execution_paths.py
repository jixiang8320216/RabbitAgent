import pathlib

import pydantic

from domains.agent.domain.entities import agent_context


class AgentExecutionPaths(pydantic.BaseModel):

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)

    base_dir : pathlib.Path

    @classmethod
    def for_context(
        cls,
        session_dir: pathlib.Path,
        context: agent_context.AgentContext,
    )->AgentExecutionPaths:
        return cls(base_dir=pathlib.Path(session_dir)/context.session_id/"agents"/context.agent_id)

    @property
    def state_path(self) -> pathlib.Path:
        return self.base_dir/"state.json"