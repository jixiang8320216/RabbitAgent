import enum

import pydantic
import typing

class TurnCompletionOutcome(enum.StrEnum):
    CONTEXT="context"
    BLOCKED="blocked"


class TurnCompletionWitness(pydantic.BaseModel):

    model_config =  pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    context_message_id: str = pydantic.Field(min_length=1)
    turn_id:str = pydantic.Field(min_length=1)
    outcome: TurnCompletionOutcome
    agent_id: str|None = pydantic.Field(default_factory=None,min_length=1)
    context_message_conut: int|None = pydantic.Field(default=None,ge=1)
    context_prefix_digest: str|None = pydantic.Field(default=None,min_length=64,max_length=64,pattern=r"^[0-9a-f]+$")


    @classmethod
    def blocked(cls,*, context_message_id:str, turn_id:str) -> typing.Self:
        return cls(
            context_message_id=context_message_id,
            turn_id=turn_id,
            outcome=TurnCompletionOutcome.BLOCKED,
        )