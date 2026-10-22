from __future__ import annotations

import typing

import pydantic

import domains.agent.domain.value_objects.message as message

JsonObject=dict[str, pydantic.JsonValue]


class ChatUsage(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    input_tokens:int = pydantic.Field(default=0, ge=0)
    output_tokens:int = pydantic.Field(default=0, ge=0)
    reasoning_tokens:int = pydantic.Field(default=0, ge=0)
    cached_tokens:int = pydantic.Field(default=0, ge=0)

class ChatStopDetails(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    type:str |None= pydantic.Field(default=None, min_length=1)
    category: str|None= pydantic.Field(default=None, min_length=1)
    explanation: str|None= pydantic.Field(default=None, min_length=1)

    @pydantic.model_validator(mode="after")
    def require_details(self)->ChatStopDetails:
        if self.type is None and self.category is None and self.explanation is None:
            raise ValueError("ChatStopDetails must not be None")
        return self

    def to_json_object(self)->JsonObject:
        payload: JsonObject= {}
        if self.type is not None:
            payload["type"] = self.type
        if self.category is not None:
            payload["category"] = self.category
        if self.explanation is not None:
            payload["explanation"] = self.explanation
        return payload

class  ChatResponse(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)

    content: str|list[message.StreamContentBlock]
    usage: ChatUsage|None=None
    finish_reason: str|None=pydantic.Field(default=None, min_length=1)
    stop_details:ChatStopDetails | None=None

    @pydantic.field_validator('finish_reason',mode="before")
    @classmethod
    def normalize_finish_reason(cls, value:str|None)->str|None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
    @pydantic.field_validator('content', mode="before")
    @classmethod
    def normalize_content(cls, value:str|list[message.StreamContentBlock] | typing.Mapping[str, object])->str|None:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            raise ValueError("chat response contexnt must be a string or a list")
        normalized: list[message.StreamContentBlock] = []
        for block in value:
            if message.is_stream_content_block(block):
                normalized.append(block)
            elif isinstance(block, typing.Mapping):
                normalized.append(message.stream_content_block_from_mapping(block))
            else:
                raise ValueError("chat response context must be a string or a list")

        return normalized