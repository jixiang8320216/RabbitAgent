import time
import typing
import json

import pydantic

import domains.agent.domain.value_objects.message as message

type JsonObject= dict[str, pydantic.JsonValue]
_JSON_OBJECT_ADAPTER= pydantic.TypeAdapter(JsonObject)
_TOOL_INPUT_CONTROL_MARKERS=("<arg_key>","<arg_value>","</arg_value>")

def _contains_control_marker_key(value: pydantic.JsonValue)->bool:
    if isinstance(value, dict):
        for key, value in value.items():
            if any(marker in key for marker in _TOOL_INPUT_CONTROL_MARKERS):
                return True
            if _contains_control_marker_key(value):
                return True
    elif isinstance(value, list):
        return any(_contains_control_marker_key(item) for item in value)
    return False

class ToolInputError(pydantic.BaseModel):
    model_config = pydantic.ConfigDict( extra="forbid", frozen=True, strict=True)
    reason:str = pydantic.Field(min_length=1)
    raw_input:str

class ToolInputStreamStats(pydantic.BaseModel):
    model_config = pydantic.ConfigDict( extra="forbid", frozen=True, strict=True)
    fragment_count:int= pydantic.Field(ge=0)
    received_chars:int= pydantic.Field(ge=0)
    reconstructed_chars:int= pydantic.Field(ge=0)
    identity_delay_ms:int=pydantic.Field(ge=0)
    first_argument_delay_ms:int=pydantic.Field(ge=0)
    duration_ms:int=pydantic.Field(ge=0)
    fragment_mode:typing.Literal["append","snapshot"]

class ToolCallStreamUpdate(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    tool_id:str = pydantic.Field(min_length=1)
    tool_name:str
    input_stream_phase: typing.Literal["identity","arguments"]
    input_preview: str
    input_fragment:str |None=None
    input_fragment_index: int |None=pydantic.Field(default=None,ge=0)
    input_fragment_kind:typing.Literal["append","snapshot"]|None=None

class ToolDeltaRoutingFailure(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    delta_id:str|None
    delta_index:int |None
    input_preview:str
    active_tool_ids:tuple[str,...]

class ToolCallResolution(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True,extra="forbid", frozen=True, strict=True)
    block: message.ToolUseBlock
    error: ToolInputError|None=None
    stream_stats: ToolInputStreamStats

class PendingToolCall(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", validate_assignment=True, strict=True)
    tool_id:str = pydantic.Field(min_length=1)
    tool_name:str
    index:int|None=None
    input_text:str=""
    input_object:JsonObject|None=None
    error:ToolInputError|None=None
    next_fragment_index:int=pydantic.Field(default=0,ge=0)
    stream_started_at: float=pydantic.Field(default_factory=time.monotonic,exclude=True)
    identity_at: float|None=pydantic.Field(default=None,exclude=True)
    first_fragment_at: float|None=pydantic.Field(default=None,exclude=True)
    last_fragment_at: float|None=pydantic.Field(default=None,exclude=True)
    fragment_count:int=pydantic.Field(default=0,ge=0)
    received_chars:int=pydantic.Field(default=0,ge=0)
    snapshot_count:int=pydantic.Field(default=0,ge=0)


    def  mark_identity(self)->bool:
        if self.identity_at is not None:
            return False
        self.identity_at = time.monotonic()
        return True
    def input_preview(self)->str:
        if self.input_object is not None:
            return json.dumps(self.input_object, ensure_ascii=False, sort_keys=False)
        return self.input_text

    def append_text(self, fragment:str)->None:
        if self.input_object is not None:
            self.error= ToolInputError(
                reason="tool input delta arrived after a complete Json object",
                raw_input=self.input_preview()+fragment)
            return
        self.input_text += fragment

    def replace_text(self,snapshot:str)->None:
        if self.input_object is not None:
            self.error= ToolInputError(
                reason="tool input delta arrived after a complete Json object",
                raw_input=snapshot,
            )
            return
        self.input_text = snapshot

    def record_fragment(
        self,
        fragment:str,
        kind:typing.Literal["append","snapshot"],
    )->None:
        if not fragment:
            return
        now = time.monotonic()
        if self.first_fragment_at is None:
            self.first_fragment_at = now
        self.last_fragment_at = now
        self.fragment_count += 1
        self.received_chars += len(fragment)
        if kind == "snapshot":
            self.snapshot_count += 1

    def stream_stats(self)->ToolInputStreamStats:
         identity_at = self.identity_at or self.stream_started_at
         first_at = self.first_fragment_at or identity_at
         last_at = self.last_fragment_at or first_at
         return ToolInputStreamStats(
             fragment_count=self.fragment_count,
             received_chars=self.received_chars,
             reconstructed_chars=len(self.input_preview()),
             identity_delay_ms=max(0, round((identity_at - self.stream_started_at)*1000)),
             first_argument_delay_ms=max(0, round((first_at - self.stream_started_at)*1000)),
             duration_ms=max(0, round((last_at - first_at)*1000)),
             fragment_mode="snapshot" if self.snapshot_count else "append",
         )

    def set_complete_input(self, value:JsonObject)->None:
        if self.input_text:
            self.error= ToolInputError(
                reason="tool input delta arrived after a complete Json object",
                raw_input=self.input_text,
            )
        self.input_object = _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)

    def allocate_fragment_index(self)->int:
        index=self.next_fragment_index
        self.next_fragment_index+=1
        return index

    def mark_routing_failure(self, active_count:int,fragment:str)->None:
        self.error= ToolInputError(
            reason=("tool delta routing failed after a complete Json object"),
            raw_input=self.input_preview()+fragment,
        )

    def resolve(self)->ToolCallResolution:
        error = self.error
        arguments:JsonObject
        if self.input_object is not None:
            arguments = self.input_object
        elif not self.input_text:
            arguments = {}
            if error is None:
                error= ToolInputError(reason="tool input delta arrived after a complete Json object", raw_input="")
        else:
            try:
                arguments = _JSON_OBJECT_ADAPTER.validate_json(self.input_text,strict=True)
            except pydantic.ValidationError as e:
                arguments={}
                if error is None:
                    error=ToolInputError(
                        reason="tool input delta arrived after a complete Json object",
                        raw_input=self.input_text,
                    )
        if _contains_control_marker_key(arguments):
            if error is None:
                error= ToolInputError(
                    reason="tool input delta arrived after a complete Json object",
                    raw_input=self.input_text or json.dumps(arguments,ensure_ascii=False),
                )
            arguments={}
        return ToolCallResolution(
            block=message.ToolUseBlock(
                type="tool_use",
                id=self.tool_id,
                name= self.tool_name,
                input=arguments,
                index=self.index,
            ),
            error=error,
            stream_stats=self.stream_stats(),
        )



















