import pydantic
import collections
import enum


class MCPScope(str,enum.Enum):
    PROJECT="project"
    USER="user"
    SYSTEM="system"

class MCPServerConfig(pydantic.BaseModel):


   @classmethod
   def from_dict(cls, data: collections.abc.Mapping[str,pydantic.JsonValue]) -> 'MCPServerConfig':
       normalized = dict(data)
       scope = normalized.get('scope', MCPScope.SYSTEM.value)
       if scope not in {member.value for member in MCPScope}:
           normalized['scope'] = MCPScope.SYSTEM.value
       return cls.model_validate(normalized)
