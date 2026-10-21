import pydantic
import secrets
import datetime as datetime_module
import domains.agent.domain.value_objects.message as message

type ToolMetadataValue= str|int|float|bool|None|list[ToolMetadataValue]|dict[str, ToolMetadataValue]
type ToolMetadata = dict[str,ToolMetadataValue]

def _new_response_id()->str:
    timestamp=datetime_module.datetime.now(datetime_module.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"{timestamp}_{secrets.token_hex(3)}"

class ToolResponse(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    content:list[message.ContentBlock]
    metadata: ToolMetadata|None=None
    stream: bool=False
    is_last:bool=False
    is_interrupted:bool=False
    id:str=pydantic.Field(default_factory=_new_response_id)
