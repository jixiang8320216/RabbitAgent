import pydantic


class ToolLoopHit(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)

    tool_name:str =pydantic.Field(min_length=1)
    args_preview:str =pydantic.Field(min_length=1)
    count:int =pydantic.Field(ge=2)
