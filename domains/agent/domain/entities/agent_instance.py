import pydantic
import datetime as datetime_module
import typing
import domains.agent.domain.entities.agent_auto_compact_suppression as agent_auto_compact_suppression
import domains.agent.domain.value_objects.conversation_context as context_module
import domains.agent.domain.entities.agent_runtime_spec as agent_runtime_spec
JsonObject =dict[str,pydantic.JsonValue]
_JSON_OBJECT_ADAPTER=pydantic.TypeAdapter(JsonObject)

class AgentInstance(pydantic.BaseModel):

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True,extra="forbid", validate_assignment=True, strict=True)

    agent_id:str =pydantic.Field(min_length=1,frozen=True)
    spec: agent_runtime_spec.AgentRuntimeSpec
    context:context_module.Context = pydantic.Field(default_factory=context_module.Context)
    role_context_dict:JsonObject = pydantic.Field(default_factory=dict)
    auto_compact_suppression:agent_auto_compact_suppression.AgentAutoCompactSuppression|None=None
    created_at:str = pydantic.Field(default_factory=lambda: datetime_module.datetime.now(tz=datetime_module.UTC).isoformat())
    revision:int = pydantic.Field(default=0,ge=0)
    is_compressing:bool=pydantic.Field(default=False)
    compression_run_id:str |None= pydantic.Field(default=None,exclude=True)
    compression_reason:str |None = pydantic.Field(default=None,exclude=True)

    def to_dict(self) -> JsonObject:
        return _JSON_OBJECT_ADAPTER.validate_python(
            {
                "agent_id":self.agent_id,
                "config":self.spec.to_dict(),
                "context":self.context.state_dict(),
                "role_context":dict(self.role_context_dict),
                "auto_compact_suppression":(
                  self.auto_compact_suppression.model_dump(mode="json")
                  if self.auto_compact_suppression is not None
                  else None,
                ),
                "created_at":self.created_at,
                 "revision":self.revision,

            }
        )

    @classmethod
    def  from_dict(cls, data: typing.Mapping[str, pydantic.JsonValue]) -> AgentInstance:
          document = _AgentInstanceDocument.model_validate(dict(data))
          ctx=context_module.Context()
          ctx.load_state_dict(document.context)
          return cls(
              agent_id=document.agent_id,
              spec = document.config,
              context=ctx,
              role_context_dict=document.role_context,
              auto_compact_suppression=document.auto_compact_suppression,
              created_at=document.created_at,
              revision=document.revision,
          )

class _AgentInstanceDocument(pydantic.BaseModel):

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    agent_id:str =pydantic.Field(min_length=1)
    config: agent_runtime_spec.AgentRuntimeSpec
    context: JsonObject = pydantic.Field(default_factory=dict)
    role_context_dict:JsonObject = pydantic.Field(default_factory=dict)
    auto_compact_suppression: agent_auto_compact_suppression.AgentAutoCompactSuppression|None=None
    created_at:str = pydantic.Field(default_factory=lambda:datetime_module.datetime.now(tz=datetime_module.UTC).isoformat())
    revision:int = pydantic.Field(default=0,ge=0)



















