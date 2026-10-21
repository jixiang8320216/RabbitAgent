import pydantic

import domains.agent.domain.value_objects.chat_response as chat_response
import domains.agent.domain.value_objects.message as message


class OpenAIResponsesObservation(pydantic.BaseModel):
    started_at:float
    usage: chat_response.ChatUsage = pydantic.Field(default_factory=chat_response.ChatUsage)
    thinking_parts:list[str] =pydantic.Field(default_factory=list)
    tool_call_ids: list[str] =pydantic.Field(default_factory=list)

    def observe(self, chunk:chat_response.ChatResponse)->tuple[str,...]:
        if chunk.usage is not None:
            self.usage = chunk.usage
        if not isinstance(chunk.content, list):
            return ()
        thinking_delta:list[str] =[]
        for block in chunk.content:
            if isinstance(block, message.ThinkingBlock):
                self.thinking_parts.append(block.thinking)
                thinking_delta.append(block.thinking)
            elif isinstance(block, (message.ToolUseBlock,message.ToolUseDeltaBlock)):
                self._observe_tool_call_id(block.id)


    def _observe_tool_call_id(self, tool_call_id:str|None)->None:
        if tool_call_id and tool_call_id not in self.tool_call_ids:
            self.tool_call_ids.append(tool_call_id)

    @property
    def reasoning_content(self)->str|None:
        reasoning_content="".join(self.thinking_parts)
        return reasoning_content or None

    def duration_ms(self, completed_at: float)->int:
        return int((completed_at - self.started_at)*1000)
