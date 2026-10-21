import pydantic


class ObservabilityPolicy(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    capture_content: bool = True
    max_string_len:int = pydantic.Field(default=4096, gt=0)
