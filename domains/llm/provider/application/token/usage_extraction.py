import pydantic

import domains.agent.domain.value_objects.chat_response as chat_contracts


class _CompletionTokenDetails(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra='ignore', from_attributes=True, strict=True)

    reasoning_tokens: int | None = None

class _PromptTokenDetails(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra='ignore', from_attributes=True, strict=True)

    cached_tokens:int|None = None


class _AnthropicUsageProjection(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra='ignore',from_attributes=True, strict=True )
    input_tokens:int| None= None
    output_tokens:int| None= None
    cache_read_input_tokens:int| None= None

class TokenExtractor:



    @staticmethod
    def from_anthropic(raw_usage:object|None)->chat_contracts.ChatUsage:
        if not raw_usage:
            return chat_contracts.ChatUsage()
        try:
            usage= _AnthropicUsageProjection.model_validate(raw_usage)
        except pydantic.ValidationError :
            return chat_contracts.ChatUsage()

        return chat_contracts.ChatUsage(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            reasoning_tokens=0,
            cached_tokens= usage.cache_read_input_tokens or 0,
        )