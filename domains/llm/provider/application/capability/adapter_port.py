import typing

import domains.llm.provider.domain.runtime_configuration as runtime_configuration
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.message_formatter as message_formatter

@typing.runtime_checkable
class LLMProviderAdapter(typing.Protocol):

    def create_model(
        self,
        token_counter:object|None=None,
        agent_name:str|None=None,
        **kwargs:object,
    )->chat_model.ChatModel:...

    def create_formatter(
        self,
        tool_args_compression_limit:int=-1,
        **kwargs:object,
    )-> message_formatter.MessageFormatter:...


    def validate_config(
        self,
        config: runtime_configuration.LLMRuntimeConfiguration,
    )-> bool: ...

    async def test_connection(
        self,
        test_message: str="Hello! Please respond wtih 'Connection successful'.'",
        max_tokens:int=50,
        timeout:float=20.0,
    )-> runtime_configuration.JsonObject   :...

    def get_provider_name(self)->str:...
