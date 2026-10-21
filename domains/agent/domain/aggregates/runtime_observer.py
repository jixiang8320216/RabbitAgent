import typing

import domains.agent.domain.value_objects.provider_retry_status as provider_retry_status

class RuntimeObserver(typing.Protocol):
    async def on_provider_retry_status(self,
        status: provider_retry_status.ProviderRetryStatus,)->None:...

    async def on_thinking_start(
        self,
        agent_name:str,
        operation_id:str,
        initial_prompt: typing.Optional[str]=None,
        tools:typing.Optional[typing.List[str]]=None,
        instance_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
       )->None:...

    async def on_thinking_end(
        self,
        agent_name:str,
        operation_id:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        tool_call_ids: typing.Optional[typing.List[str]]=None,
        reasoning_content:typing.Optional[str]=None,
        cached_tokens:int=0,
        instance_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
    )->None:...


    async def on_thinking_content(
        self,
        agent_name:str,
        operation_id:typing.Optional[str]=None,
        content:str="",
        content_kind:typing.Optional[str]=None,
    )->None:...

    async def on_text_delta(
        self,
        agent_name:str,
        delta:str,
        operation_id:typing.Optional[str]=None,
    )->None:...

    async def on_text_stream_end(
        self,
        agent_name:str,
        operation_id:typing.Optional[str]=None, )->None:...


    async def on_tool_call_streaming(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:str,
        operation_id:typing.Optional[str]=None,
        input_preview:str="",
        input_stream_phase:typing.Optional[typing.Literal["identity","arguments"]]=None,
        input_fragment:typing.Optional[str]=None,
        input_fragment_index:typing.Optional[int]=None,
        input_fragment_kind:typing.Optional[str]=None,
    )->None:...

    async def on_llm_complete(
        self,
        provider:str,
        model:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        duration_ms:int=0,
        error: typing.Optional[str]=None,
        error_obj:typing.Optional[BaseException]=None,
        cached_tokens:int=0,
        is_proxy:bool=False,
        is_streaming:bool=False,
    )->None:...

    def is_interrupted(self)->bool:...

class NoOpObserver(typing.Protocol):
    async def on_provider_retry_status(
        self,
        status: provider_retry_status.ProviderRetryStatus,
    )->None:
       del status

    async def on_thinking_start(
        self,
        agent_name:str,
        operation_id:str,
        initial_prompt: typing.Optional[str]=None,
        tools:typing.Optional[typing.List[str]]=None,
        instance_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
       )->None:
        pass

    async def on_thinking_end(
        self,
        agent_name:str,
        operation_id:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        tool_call_ids: typing.Optional[typing.List[str]]=None,
        reasoning_content:typing.Optional[str]=None,
        cached_tokens:int=0,
        instance_id:typing.Optional[str]=None,
        model_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
    )->None:
        pass

    async def on_thinking_content(
        self,
        agent_name:str,
        operation_id:typing.Optional[str]=None,
        content:str="",
        content_kind:typing.Optional[str]=None,
    )->None:
        pass

    async def on_text_delta(
        self,
        agent_name:str,
        delta:str,
        operation_id:typing.Optional[str]=None,
    )->None:
        pass

    async def on_text_stream_end(
        self,
        agent_name:str,
        operation_id:typing.Optional[str]=None, )->None:
        pass


    async def on_tool_call_streaming(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:str,
        operation_id:typing.Optional[str]=None,
        input_preview:str="",
        input_stream_phase:typing.Optional[typing.Literal["identity","arguments"]]=None,
        input_fragment:typing.Optional[str]=None,
        input_fragment_index:typing.Optional[int]=None,
        input_fragment_kind:typing.Optional[str]=None,
    )->None:
        pass

    async def on_llm_complete(
        self,
        provider:str,
        model:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        duration_ms:int=0,
        error: typing.Optional[str]=None,
        error_obj:typing.Optional[BaseException]=None,
        cached_tokens:int=0,
        is_proxy:bool=False,
        is_streaming:bool=False,
    )->None:...

    def is_interrupted(self)->bool:
        return False


class NoOPRuntimeObserver:
    """**可实例化**的空 observer（``NoOpObserver`` 是 Protocol，不能 new）。

    组件在「外部没给 observer」时拿它兜底——例如
    ``ProviderChatModelBase.__init__`` 里的 ``observer or NoOPRuntimeObserver()``。
    所有回调都是 no-op，``is_interrupted()`` 恒为 ``False``。
    """

    async def on_provider_retry_status(
        self,
        status: provider_retry_status.ProviderRetryStatus,
    )->None:
        del status

    async def on_thinking_start(
        self,
        agent_name:str,
        operation_id:str,
        initial_prompt: typing.Optional[str]=None,
        tools:typing.Optional[typing.List[str]]=None,
        instance_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
    )->None:
        del agent_name, operation_id, initial_prompt, tools, instance_id, operation_kind

    async def on_thinking_end(
        self,
        agent_name:str,
        operation_id:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        tool_call_ids: typing.Optional[typing.List[str]]=None,
        reasoning_content:typing.Optional[str]=None,
        cached_tokens:int=0,
        instance_id:typing.Optional[str]=None,
        model_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
    )->None:
        del (
            agent_name, operation_id, input_tokens, output_tokens, reasoning_tokens,
            tool_call_ids, reasoning_content, cached_tokens, instance_id, model_id,
            operation_kind,
        )

    async def on_thinking_content(
        self,
        agent_name:str,
        operation_id:typing.Optional[str]=None,
        content:str="",
        content_kind:typing.Optional[str]=None,
    )->None:
        del agent_name, operation_id, content, content_kind

    async def on_text_delta(
        self,
        agent_name:str,
        delta:str,
        operation_id:typing.Optional[str]=None,
    )->None:
        del agent_name, delta, operation_id

    async def on_text_stream_end(
        self,
        agent_name:str,
        operation_id:typing.Optional[str]=None,
    )->None:
        del agent_name, operation_id

    async def on_tool_call_streaming(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:str,
        operation_id:typing.Optional[str]=None,
        input_preview:str="",
        input_stream_phase:typing.Optional[typing.Literal["identity","arguments"]]=None,
        input_fragment:typing.Optional[str]=None,
        input_fragment_index:typing.Optional[int]=None,
        input_fragment_kind:typing.Optional[str]=None,
    )->None:
        del agent_name, tool_name, tool_id, operation_id, input_preview
        del input_stream_phase, input_fragment, input_fragment_index, input_fragment_kind

    async def on_llm_complete(
        self,
        provider:str,
        model:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        duration_ms:int=0,
        error: typing.Optional[str]=None,
        error_obj:typing.Optional[BaseException]=None,
        cached_tokens:int=0,
        is_proxy:bool=False,
        is_streaming:bool=False,
    )->None:
        del (
            provider, model, input_tokens, output_tokens, reasoning_tokens, duration_ms,
            error, error_obj, cached_tokens, is_proxy, is_streaming,
        )

    def is_interrupted(self)->bool:
        return False