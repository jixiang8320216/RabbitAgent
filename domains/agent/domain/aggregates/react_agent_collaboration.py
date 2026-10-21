
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.value_objects.chat_response as chat_response
import typing
import domains.agent.domain.value_objects.message as message
import domains.agent.domain.value_objects.tool_response as tool_response_model
import domains.agent.domain.entities.agent_context as agent_context_module
from domains.agent.domain.entities import agent_transcript

type ToolResult = tuple[message.ToolUseBlock,tool_response_model.ToolResponse]

class AgentResultStorePort(typing.Protocol):

    def save_result(
        self,
        context: agent_context_module.AgentContext,
        text:str,
    ):...

class ReactAgentRuntime(typing.Protocol):

    async def _call_model_with_error_recovery(
         self,
         formatted:list[chat_model.JsonObject],
         tool_schema:list[chat_model.JsonObject],
          *,
          iteration:int,
    )->typing.AsyncGenerator[chat_response.ChatResponse,None]:...

    async def _emit_tool_call_streaming(
         self,
         tool_id:str,
         tool_name:str,
         input_stream_phase:str,
         input_preview:str,
         input_fragment:str|None,
         input_fragmnent_index:int|None,
         input_fragment_kind:str|None,
    )->None:...

    def _append_transcript_message(
         self,
         runtime_message:message.Msg,
        *,
        iteration:int,
    )->None:...

    def _append_transcript_event(self, event: agent_transcript.AgentTranscriptEvent)->None:...

    def _persist_state(self)->None:...

    def _get_execution_store(self)->AgentResultStorePort:...

    async def _dispatch_post_tool_batch(self, tool_results:list[ToolResult])->None:...

    async def _summarize(self)-> message.Msg:...

class SubagentRuntime(typing.Protocol):
      ...