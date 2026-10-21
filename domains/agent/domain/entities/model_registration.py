import collections
import typing

import pydantic
import domains.plugin.domain as plugin_domain

ModelRegistrationSource=typing.Annotated[
    str,
    pydantic.Field(pattern=r"^(?:builtin|plugin:.+)$"),
]

class RegisteredMode(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed = True,
        extra = "forbid",
        frozen=True,
        strict = True
    )
    declaration: pydantic.InstanceOf[plugin_domain.ModeDeclaration]
    builder_override : collections.abc.Callable[...,object]|None=None
    source: ModelRegistrationSource

class AvailableModeDescriptor(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True
    )

    value:str = pydantic.Field(min_length=1)
    label: str = pydantic.Field(min_length=1)
    description: str
    source: ModelRegistrationSource