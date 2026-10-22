from __future__ import annotations

import pydantic

from domains.llm.provider.domain import provider_identity
import collections.abc
import collections.abc as abc


class LLMProviderProfile(pydantic.BaseModel):

    id:str=pydantic.Field(
        default_factory=provider_identity.generate_profile_id,
        description="配置唯一标识符（自动生成）",
        min_length=8,
        max_length=50,
        examples=["gpt4","claude-3"],
        json_schema_extra={"x-ui-hidden":True, "x-ui-reason":"auto-generated"},
    )

    @classmethod
    def from_dict(cls, data:collections.abc.Mapping[str,object],validate:bool=True)-> LLMProviderProfile:
        #todo
        pass


__all__ = ["LLMProviderProfile"]
