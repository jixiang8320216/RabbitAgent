import pydantic
import domains.hook.domain.inputs as hook_inputs

class PromptExpansion(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid",frozen=True)

    expansion_type:hook_inputs.PromptExpansionType
    command_name:str= pydantic.Field(min_length=1)
    command_args:str=""
    command_source:str=pydantic.Field(min_length=1)
    prompt:str=pydantic.Field(min_length=1)
    expanded_prompt:str
