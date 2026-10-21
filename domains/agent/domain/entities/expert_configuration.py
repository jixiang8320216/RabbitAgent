import pathlib
import typing
import enum

import pydantic
import domains.mcp.domain.server_configuration as mcp_server_configuraion

EXPERT_SCHEMA_VERSION=2

class ExperResourcesBingMode(str, enum.Enum):
    AUTO="auto"
    MANUAL="manual"

class AssociatedExpertRef(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", forzen=True, strict=True)
    expert_id:str
    display_name:str

class ExpertResourceBing(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", forzen=True, strict=True)
    model:ExperResourcesBingMode = ExperResourcesBingMode.AUTO
    names: tuple[str,...]=()
    expert_refs:tuple[AssociatedExpertRef,...]=()

class ExpertResourceBings(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", forzen=True, strict=True)

    tools:ExpertResourceBing = pydantic.Field(default_factory=ExpertResourceBing)
    skills:ExpertResourceBing = pydantic.Field(default_factory=ExpertResourceBing)
    mcp_servers:ExpertResourceBing = pydantic.Field(default_factory=ExpertResourceBing)
    agents:ExpertResourceBing = pydantic.Field(default_factory=ExpertResourceBing)

class ExpertSkillType(str, enum.Enum):
    ROLE="role"
    UTILITY="utility"

class ExpertPackageMetadata(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", forzen=True, strict=True)
    version:str="1.0.0"
    display:dict[str,dict[str,str]]=pydantic.Field(default_factory=dict)
    category:str=""
    starter_prompts:tuple[str,...]=()

class ExpertConfiguration(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", forzen=True, strict=True)

    schema_version:typing.Literal[EXPERT_SCHEMA_VERSION]= EXPERT_SCHEMA_VERSION
    expert_it:str
    name:str
    description:str
    model_id:str|None=None
    max_tokens: int=pydantic.Field(default_factory=80_000,ge=0)
    skill_type : ExpertSkillType = ExpertSkillType.UTILITY
    capabilities: tuple[str,...]=()
    limitations: tuple[str,...]=()
    trigger_keywords: tuple[str,...]=()
    load_agents_md: bool = True
    resources: ExpertResourceBings = pydantic.Field(default_factory=ExpertResourceBings)
    strict_mco_servers: bool = False
    hooks:pydantic.JsonValue = pydantic.Field(default_factory=dict)
    package: ExpertPackageMetadata = pydantic.Field(default_factory=ExpertPackageMetadata)
    extensions: dict[str, pydantic.JsonValue] = pydantic.Field(default_factory=dict)


class ExpertReadinessStatus(str,enum.Enum):
    READY = "ready"
    BLOCKED = "blocked"

class ExpertReadinessDiagnostic(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", forzen=True, strict=True)
    code:str
    message:str
    resource_id:str|None=None
    action:str |None=None

class ExpertReadiness(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", forzen=True, strict=True)
    status: ExpertReadinessStatus = ExpertReadinessStatus.READY
    diagnostics: tuple[ExpertReadinessDiagnostic,...]=()



class ExpertResourceLock(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid",forzen=True,strict=True)
    mcp_resource_id:str
    field:typing.Literal["env","headers"]
    key:str
    required:bool=True

class ResolvedExpertConfiguration(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid",forzen=True,strict=True)

    configuration: ExpertConfiguration
    prompt:str
    directory:pathlib.Path
    resource_root: pathlib.Path |None=None
    revision:str
    source_category:typing.Literal["builtin","system","custom"]
    package_skill_names: tuple[str,...]=()
    package_mcp_server_names:tuple[str,...]=()
    package_tool_names:tuple[str,...]=()
    package_mcp_configs:tuple[mcp_server_configuraion.MCPServerConfig,...]=()
    resource_lock:ExpertResourceLock|None=None
    readiness: ExpertReadiness = pydantic.Field(default_factory=ExpertReadiness)

@typing.runtime_checkable
class ExplicitConfigurationSource(typing.Protocol):

    def list_expert(self)->tuple[ResolvedExpertConfiguration,...]:
        ...