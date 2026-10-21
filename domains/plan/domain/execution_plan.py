import pydantic
import typing
import datetime as datetime_module
import shortuuid

PlanState=typing.Literal["todo","in_process","done","abandoned"]

def _get_timestamp()->str:
    return datetime_module.datetime.now(tz=datetime_module.UTC).isoformat(timespec="milliseconds")

class Plan(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    name:str = pydantic.Field(
        min_length=1,
        description="The name of the subtask",)

    id:str = pydantic.Field(
        default_factory=shortuuid.uuid,
        min_length=1,
        description="The unique identifier of the subtask",
    )

    description:str = pydantic.Field(
        min_length=1,
        description="The detailed description of what the subtask is supposed to do",
    )

    expected_outcome:str = pydantic.Field(
        min_length=1,
        description="The expected outcome that marks the subtask as successfully done",
    )

    subtasks:list[Subtask] = pydantic.Field(
        min_length=1,
        description="The actual outcome recorded once the subtask reaches a terminal state",
    )

    created_at:str = pydantic.Field(
        default_factory=_get_timestamp,
        description="ISO-8601 timestamp of when the subtask was created",
    )

    state:PlanState = pydantic.Field(
        default="todo",
        description=(
            "The current execution state of the subtask "
            "(pending / in_progress / completed / failed / skipped)"
        ),
    )

    finished_at:str|None = pydantic.Field(
        default=None,
        description="ISO-8601 timestamp of when the subtask reached a terminal state (None while running)",
    )

    outcome:str|None = pydantic.Field(
        default=None,
        description="The actual outcome recorded once the subtask reaches a terminal state",
    )


class Subtask(pydantic.BaseModel):

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)

    name:str = pydantic.Field(
        min_length=1,
        description="The name of the subtask",)

    id:str = pydantic.Field(
        default_factory=shortuuid.uuid,
        min_length=1,
        description="The unique identifier of the subtask",
    )

    description:str = pydantic.Field(
        min_length=1,
        description="The detailed description of what the subtask is supposed to do",
    )

    expected_outcome:str = pydantic.Field(
        min_length=1,
        description="The expected outcome that marks the subtask as successfully done",
    )

    outcome:str|None = pydantic.Field(
        default=None,
        description="The actual outcome recorded once the subtask reaches a terminal state",
    )

    state:PlanState = pydantic.Field(
        default="todo",
        description=(
            "The current execution state of the subtask "
            "(pending / in_progress / completed / failed / skipped)"
        ),
    )

    created_at:str = pydantic.Field(
        default_factory=_get_timestamp,
        description="ISO-8601 timestamp of when the subtask was created",
    )

    finished_at:str|None = pydantic.Field(
        default=None,
        description="ISO-8601 timestamp of when the subtask reached a terminal state (None while running)",
    )


