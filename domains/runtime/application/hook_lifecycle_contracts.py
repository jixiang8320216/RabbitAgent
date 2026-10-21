import pydantic
import typing
import domains.hook.domain.inputs as hook_inputs

class HookLifecycleMetadata(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    session_id:str = pydantic.Field(min_length=1)
    correlation_id:str = pydantic.Field(min_length=1)
    cwd:str | None = None
    permission_mode: hook_inputs.PermissionMode |None= None
    prompt_id:str|None = None
    effort: hook_inputs.HookEffort |None= None
    agent_id:str | None = None
    agent_type:str | None = None
    current_model:str | None = None
    current_model_profile_id:str | None = None
    compatible_model_profiles:dict[str,str]=pydantic.Field(default_factory=dict)
    environment:dict[str,str]=pydantic.Field(default_factory=dict)
    trusted_project:bool =True
    test:bool =False

class HookCommonInput(typing.TypedDict):
    session_id:str
    transcript_path:str
    cwd:str
    permission_mode:hook_inputs.PermissionMode |None
    prompt_id:str|None
    effort : hook_inputs.HookEffort|None
    agent_id:str | None
    agent_type:str | None

class HookAgentCommonInput(typing.TypedDict):
    session_id:str
    transcript_path:str
    cwd:str
    permission_mode:hook_inputs.PermissionMode |None
    prompt_id:str|None
    effort : hook_inputs.HookEffort|None
    agent_id:str | None
    agent_type:str | None


