import pydantic

JsonObject=dict[str,pydantic.JsonValue]

class AgentRuntimeSpec(pydantic.BaseModel):

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True,extra="forbid", validate_assignment=True, strict=True)

    agent_id:str =pydantic.Field(min_length=1)
    role:str =pydantic.Field(min_length=1)
    name:str =pydantic.Field(min_length=1)
    description:str =""
    model:str=""
    system_prompt:str =""
    max_tokens:int =pydantic.Field(default=0,ge=0)
    mcp_servers:list[str]=pydantic.Field(default=list)
    skill_refs:list[str]=pydantic.Field(default=list)
    plugin_refs:list[str]=pydantic.Field(default=list)
    owner:str|None=None
    visibility:str|None=None
    tenant:str|None=None
    extra: JsonObject = pydantic.Field(default_factory=dict)
