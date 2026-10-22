import typing

import pydantic

from domains.plan.domain import execution_plan
import domains.observability.domain.tool_result_projection as tool_result_projection
import domains.agent.domain.value_objects.provider_retry_status as provider_retry_status


@typing.runtime_checkable
class RabbitObserver(typing.Protocol):


    def on_application_start(self, version:str) -> None:
       """

       :param version:
       :return:
       """

    def on_application_end(self) -> None:
        pass

    async def get_user_input(self)->typing.Optional[str]:
        pass

    async def on_agent_call_start(self,
       agent_name:str,
       task:str,
       instance_id: typing.Optional[str]=None,
       parent_id: typing.Optional[str]=None,
       agent:object|None=None,
        ):
        pass

    async def on_agent_call_end(self,
        agent_name:str,
        end_timestamp:str,
        error:typing.Optional[str]=None,
        instance_id:typing.Optional[str]=None,
        parent_id:typing.Optional[str]=None,
        )->None:
        pass

    async def on_agent_reasoning_start(self,
        agent_name:str,
        thought:str,
    )->None:
        pass

    async def on_agent_reasoning_end(self,agent_name:str,thought:typing.Optional[str], end_timestamp:str)->None:
        pass

    async def on_agent_replay_start(self,
        text:str,
    )->None:
        pass

    async def on_agent_replay_end(self,text:str, end_timestamp:str, input_tokens:int=0, output_tokens:int=0, max_tokens:int=128000)->None:
        ...

    async def on_tool_call_start(
        self,
        agent_name:str,
        tool_name:str,
        args_summary:str,
        tool_id: typing.Optional[str]=None,
        timeout:typing.Optional[int]=None,
        cwd:typing.Optional[bool]=None,
        timestamp:typing.Optional[int]=None,
        thinking_operation_id: typing.Optional[str]=None,
        parent_instance_id:typing.Optional[str]=None,
        model_id: typing.Optional[str]=None,
        display_name:typing.Optional[str]=None,
    )->None:
        ...
    async def on_tool_call_end(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:typing.Optional[str]=None,
        timestamp:typing.Optional[int]=None,
        error:typing.Optional[bool]=None,
        result_summary:typing.Optional[str]=None,
        parent_instance_id:typing.Optional[str]=None,
        status:typing.Optional[str]=None,
        model_id:typing.Optional[str]=None,
        display_name:typing.Optional[str]=None,
        external_data_cards: typing.Optional[list[dict[str,str|None]]]=None,
        structured_result: typing.Optional[tool_result_projection.StructuredToolResult]=None,
    )->None:...

    async def on_plan_update(self,
        agent_name:str,
        plan_subtasks: typing.List[execution_plan.Subtask],
        instance_id:typing.Optional[str]=None,
    )->None:...

    async def on_agent_long_term_memory_end(self,
        agent_name:str, context: typing.Optional[str], end_timestamp:str)->None:...

    async def on_command_result(self, message:str)->None:...


    async def on_command_warning(self, message:str)->None:...


    async def on_error(self, source:str, error_msg:str, details:typing.Optional[str]=None)->None:...


    async def on_thinking_operation_start(self,
        agent_name:str,
        operation_id:str,
        initial_prompt: typing.Optional[str]=None,
        available_tools: typing.Optional[typing.List[str]]=None,
        instance_id:typing.Optional[str]=None,
        parent_instance_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
        )->None:...

    async def on_thinking_content_update(self,
        agent_name:str,
        operation_id:str,
        content:str,
        content_kind: typing.Optional[str]=None,
        instance_id:typing.Optional[str]=None,
        )->None:...

    async def on_provider_retry_status(self,
        status:provider_retry_status.ProviderRetryStatus,
        )->None:...


    async def on_thinking_operation_end(
        self,
        agent_name:str,
        operation_id:str,
        final_content:typing.Optional[str],
        end_timestamp:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        tool_call_count: int=0,
        has_tool_calls: bool=False,
        tool_call_ids: typing.Optional[typing.List[str]]=None,
        reasoning_content: typing.Optional[str]=None,
        cached_tokens:int=0,
        instance_id:typing.Optional[str]=None,
        mode_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
    )->None:...

    async def on_tool_call_streaming(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:str,
        operation_id: str,
        input_preview:str,
        input_stream_phase: typing.Optional[typing.Literal["identity","arguments"]]=None,
        input_fragment:typing.Optional[str]=None,
        input_fragment_index:typing.Optional[int]=None,
        input_fragment_kind: typing.Optional[str]=None,
        instance_id:typing.Optional[str]=None,
    )->None:...

    async def on_tool_output_streaming(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:str,
        delta: str,
        stream_kind:str,
        operation_id: str,
        instance_id:typing.Optional[str]=None,
    )->None:...

    async def on_token_update(
        self,
        agent_name:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        cached_tokens:int=0,
        model:typing.Optional[str]=None,
    )->None:...

    async def on_agent_text_stream(
        self,
        agent_name:str,
        text:str,
        timestamp:typing.Optional[str]=None,
        operation_id:typing.Optional[int]=None,
        instance_id:typing.Optional[str]=None,
    )->None:...

    async def on_agent_text_stream_end(
        self,
        agent_name:str,
        timestamp:typing.Optional[str]=None,
    )->None:...


    async def on_generate_response(
        self,
        agent_name:str,
        response: str,
        is_final: bool=False,
        instance_id:typing.Optional[str]=None,
    )->None:...

    async def on_board_message(
            self,
            sender: str,
            content: str,
            instance_id: str|None = None,
            **kwargs,
    ) -> None: ...

    def set_session_context(self, session_id:str)->None:...


    def clear_session_context(self)->None:...


    def on_session_start(self,  session_id:str, project_name:str)->None:...


    def on_session_end(self,  session_id:str, reason:str="normal")->None:...


    async def on_hook_event(self, event_type: str, data:dict[str, pydantic.JsonValue])->None:...

    async def on_topic_updated(self, session_id: str,  topic:str)->None:...


# 兼容调用点的拼写：``rabbit_application`` 用 ``RabbitObServer``（大写 S），
# 权威定义为 ``RabbitObserver``，这里导出别名。
RabbitObServer = RabbitObserver