import collections

import pydantic
import pathlib
import weakref
import domains.agent.domain.entities.agent_role as agent_role_module
import typing

import domains.llm.provider.domain.openai_app_protocol as openai_app_protocol
import domains.agent.domain.entities.expert_configuration as expert_configuration_module
import domains.plugin.domain as plugin_contracts

@typing.runtime_checkable
class AgentRuntimeApp(typing.Protocol):
    @property
    def app_mode(self)->str|None:...

@typing.runtime_checkable
class AgentRuntimeModelConfig(typing.Protocol):

    @property
    def profile_id(self)->str|None:...

    @property
    def provider(self)->str:...

    @property
    def api_protocol(self)->openai_app_protocol.OpenAIAPIProtocol:...

    @property
    def base_url(self)->str|None:...

    @property
    def model_name(self)->str:...

    @property
    def api_key(self)->str:...

    @property
    def temperature(self)->float:...

    @property
    def max_tokens(self)->int|None:...

    @property
    def context_window_size(self)->int|None:...

    @property
    def reasoning_effort(self)->str|None:...

    @property
    def service_tier(self)->str|None:...

    @property
    def thinking_model(self)->bool:...

    @property
    def use_proxy(self)->bool|None:...

    @property
    def http_proxy(self)->str|None:...

    @property
    def https_proxy(self)->str|None:...

    @property
    def proxy_username(self)->str|None:...

    @property
    def proxy_password(self)->str|None:...

    @property
    def custom_headers(self)->dict[str,str]|None:...

    @property
    def metadata(self)->dict[str,pydantic.JsonValue]|None:...

AgentRuntimeSkillType=typing.Literal["role","utility"]
AgentResourceBindingMode=typing.Literal["auto","manual"]
AgentRuntimeSlot =plugin_contracts.AgentSlot

class AgentRuntime(pydantic.BaseModel):

    model_config= pydantic.ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        strict=True,
        validate_assignment=True,
    )
    #Identity
    name:str=pydantic.Field(min_length=1)
    description:str
    system_prompt:str
    max_tokens:int = pydantic.Field(ge=0)

    #llm
    llm_config:AgentRuntimeModelConfig
    model_id:str|None=None
    expert_id:str|None=None
    expert_memory_file:pathlib.Path | None=None

    #capabilities
    mcp_servers: list[str]=pydantic.Field(default_factory=list)
    skill_definition:str|None=None
    skill_name:str|None=None
    skill_dir: pathlib.Path | None=None
    is_skill_based:bool=False
    capabilities:list[str]=pydantic.Field(default_factory=list)
    limitations:list[str]=pydantic.Field(default_factory=list)
    trigger_keywords:list[str]=pydantic.Field(default_factory=list)
    source_path:pathlib.Path|None=None
    resource_root:pathlib.Path|None=None
    scope:str="system"
    resource_scope:str="standard"
    allowed_tools:list[str]|None=None
    allowed_agents:list[str] =pydantic.Field(default_factory=list)
    allowed_agent_ids:list[str]=pydantic.Field(default_factory=list)
    skill_refs:list[str]|None=None
    strict_mcp_servers:bool=False
    tool_binding_mode: AgentResourceBindingMode="auto"
    skill_binding_mode: AgentResourceBindingMode="auto"
    mcp_binding_mode: AgentResourceBindingMode="auto"
    agent_binding_mode:AgentResourceBindingMode="auto"
    resource_bindings_resolved: bool=False
    package_resource_only: bool=False
    package_skill_names:list[str]=pydantic.Field(default_factory=list)
    package_mcp_server_names:list[str]=pydantic.Field(default_factory=list)
    package_tool_names:list[str]=pydantic.Field(default_factory=list)
    package_mcp_configs:list[str]=pydantic.Field(default_factory=list)
    readiness: expert_configuration_module.ExpertReadiness=pydantic.Field(default_factory=expert_configuration_module.ExpertReadiness)
    load_agents_md:bool=False
    system_prompt_file:str=""
    include_builtin_tools: bool=True
    include_mcp_tools:bool=True
    include_agent_tools:bool=True
    include_plan_notebook:bool=False
    include_plugin_tools:bool=True
    max_iters:int|None=pydantic.Field(default=None,ge=1)
    parallel_tool_calls: bool=True
    skill_type: AgentRuntimeSkillType="role"
    sub_agent_names:list[str]=pydantic.Field(default_factory=list)
    agent_role:agent_role_module.AgentRole|None=None
    agent_slot:AgentRuntimeSlot|None=None
    app_ref: weakref.ReferenceType[AgentRuntimeApp]|None=None

    @classmethod
    def from_snapshot(cls,
                      snapshot: collections.abc.Mapping[str,object],
                      llm_config:AgentRuntimeModelConfig,
                      )->AgentRuntime:
        source_path_value=snapshot.pop("source_path")
        resource_path_value=snapshot.pop("resource_root")
        agent_role_value=snapshot.pop("agent_role")
        agent_slot_value=snapshot.pop("agent_slot")
        try:
            hydrated_agent_role=agent_role_module.AgentRole(str(agent_role_value)) if agent_role_value else None
        except ValueError:
            hydrated_agent_role=None
        return cls.model_validate(
            {
                "name":str(snapshot.get("name") or ""),
                "description":str(snapshot.get("description") or ""),
                "system_prompt":str(snapshot.get("system_prompt") or ""),
                "max_tokens":int(snapshot.get("max_tokens") or 0),
                "llm_config":llm_config,
                "model_id":str(snapshot["model_id"] if snapshot.get("model_id") else None),
                "expert_id":str(snapshot["expert_id"] if snapshot.get("expert_id") else None),
                "expert_memory_file":(
                    pathlib.Path(str(snapshot.get("expert_memory_file")))
                    if snapshot.get("expert_memory_file") else None
                ),
                "mcp_servers":snapshot.get("mcp_servers") or [],
                "allowed_tools":snapshot.get("allowed_tools"),
                "allowed_agents":snapshot.get("allowed_agents") or [],
                "allowed_agent_ids":snapshot.get("allowed_agent_ids"),
                "skill_refs":snapshot.get("skill_refs"),
                "strict_mcp_servers":bool(snapshot.get("strict_mcp_servers",False)),
                "tool_binding_mode":str(snapshot.get("tool_binding_mode","auto")),
                "skill_binding_mode":str(snapshot.get("skill_binding_mode","auto")),
                "mcp_binding_mode":str(snapshot.get("mcp_binding_mode","auto")),
                "agent_binding_mode":str(snapshot.get("agent_binding_mode","auto")),
                "resource_bindings_resolved":bool(snapshot.get("resource_bindings_resolved",False)),
                "load_agents_md":bool(snapshot.get("load_agents_md",True)),
                "system_prompt_file":str(snapshot.get("system_prompt_file") or ""),
                "include_builtin_tools":bool(snapshot.get("include_builtin_tools",True)),
                "include_mcp_tools": bool(snapshot.get("include_mcp_tools",True)),
                "include_agent_tools":bool(snapshot.get("include_agent_tools",True)),
                "include_plan_notebook":bool(snapshot.get("include_plan_notebook",False)),
                "include_plugin_tools":bool(snapshot.get("include_plugin_tools",True)),
                "max_iters":int(snapshot.get("max_iters")),
                "parallel_tool_calls":bool(snapshot.get("parallel_tool_calls",True)),
                "skill_type":str(snapshot.get("skill_type") or "role"),
                "sub_agent_names":snapshot.get("sub_agent_names") or [],
                "capabilities":snapshot.get("capabilities") or [],
                "limitations":snapshot.get("limitations") or [],
                "trigger_keywords":snapshot.get("trigger_keywords") or [],
                "source_path":(pathlib.Path(str(source_path_value)) if source_path_value else None),
                "resource_root":(pathlib.Path(str(resource_path_value)) if resource_path_value else None),
                "scope":str(snapshot.get("scope") or "system"),
                "resource_scope":str(snapshot.get("resource_scope") or "standard"),
                "readiness": expert_configuration_module.ExpertReadiness.model_validate(
                    snapshot.get("readiness") or {},
                    strict=False,
                ),
                "agent_role": hydrated_agent_role,
                "agent_slot":(
                    plugin_contracts.AgentSlot.model_validate(agent_slot_value)
                    if agent_slot_value is not None
                    else None
                )
            }
        )

    def ensure_ready(self) -> None:
        """装配前自检：本声明是否可以进入装配流程。

        只有 ``readiness`` 被显式标记为 ``BLOCKED`` 时才抛错——那是「声明自己
        知道自己跑不起来」的情况（缺依赖、资源锁未满足等），早失败好过带着
        半套工具跑下去。

        .. warning::
           这里**不要**去翻 ``resource_bindings_resolved``。真正的资源绑定
           （MCP 自动发现、技能挂载）由 ``AgentToolkitBuilder`` 依据
           ``auto_discover = mcp_binding_mode != "manual" and not
           resource_bindings_resolved`` 决定；提前把它置 ``True`` 会静默关掉
           MCP 自动发现，表现是「agent 起来了但一个 MCP 工具都没有」。

        可重复调用：只检查，不改状态。
        """
        if self.readiness.status is not expert_configuration_module.ExpertReadinessStatus.READY:
            diagnostics = "; ".join(
                getattr(diagnostic, "message", "") for diagnostic in self.readiness.diagnostics
            ).strip()
            raise RuntimeError(
                f"agent runtime {self.name!r} is not ready"
                + (f": {diagnostics}" if diagnostics else "")
            )
