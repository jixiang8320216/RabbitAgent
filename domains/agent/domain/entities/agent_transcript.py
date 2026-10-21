import enum

import pydantic
import datetime as datetime_module
import domains.agent.domain.value_objects.chat_response as chat_contracts

class AgentTranscriptEventType(enum.StrEnum):
    ITERATION_STARTED = "iteration_start"
    TOOL_STARTED = "tool_start"
    TOOL_COMPLETED = "tool_end"
    RESPONSE_TRUNCATED = "reACT_truncated"

class AgentTranscriptMetadata(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    iteration:int =pydantic.Field(ge=0)

class AgentTranscriptEvent(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    event_type:AgentTranscriptEventType
    timestamp:datetime_module.datetime
    iteration: int |None=pydantic.Field(default=None,ge=0)
    agent_name:str |None=None
    tool_names:tuple[str,...]=()
    finish_reason:str |None=None
    stop_details:chat_contracts.ChatStopDetails |None=None
    text_length:int|None=pydantic.Field(default=None,ge=0)
    tool_count:int|None=pydantic.Field(default=None,ge=0)
