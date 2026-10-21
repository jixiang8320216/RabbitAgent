import pydantic
import domains.tool.command.domain.subcategory as subcategory_domain

_TOOL_METADATA_SUBCATEGORY_KEY="gen_ai.tool.metadata.subcategory"

class CommandObservabilityMetadataResolver:

    # def __init__(
    #     self,
    #     *,
    #     tool_name:str,
    #     tool_input:dict[str, pydantic.JsonValue],
    # )->dict[str, str]|None:
    #     subcategory = subcategory_domain.classify_command_subcategory(tool_input)
    #     if subcategory is None:
    #         return None
    #     return {_TOOL_METADATA_SUBCATEGORY_KEY:subcategory}


    def resolve(self, tool_name:str,tool_id:str|None=None)->dict[str, object]|None:
         return {}