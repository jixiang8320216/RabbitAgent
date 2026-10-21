import pydantic

type JsonObject = dict[str, pydantic.JsonValue]

class _HookPayload(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra='forbid', strict=True, validate_assignment=True)

class ChatHeadersInput(_HookPayload):
    headers :dict[str, str] = pydantic.Field(default_factory=dict)

class ChatHeadersOutput(_HookPayload):
    headers :dict[str, str] = pydantic.Field(default_factory=dict)

class ChatParamsInput(_HookPayload):
    temperature:float|None=None
    top_p: float|None=None
    top_k:int|None=None
    max_tokens:int|None=None
    options: JsonObject = pydantic.Field(default_factory=dict)

class ChatParamsOutput(_HookPayload):
    temperature:float|None=None
    top_p: float|None=None
    top_k:int|None=None
    max_tokens:int|None=None
    options: JsonObject = pydantic.Field(default_factory=dict)

class MessageTransformOutput(_HookPayload):
    messages:list[JsonObject] = pydantic.Field(default_factory=list)

class MessageTransformInput(_HookPayload):
    messages:list[JsonObject] = pydantic.Field(default_factory=list)