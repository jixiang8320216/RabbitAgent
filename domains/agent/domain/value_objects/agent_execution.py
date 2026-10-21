import pydantic


class AgentPromptProvenance(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)

    original_system_prompt:str
    effective_system_prompt:str
    optimization_applied:bool