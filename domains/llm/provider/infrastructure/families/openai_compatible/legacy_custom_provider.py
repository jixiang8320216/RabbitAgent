import logging

import domains.llm.provider.infrastructure.families.openai_compatible.provider as openai_provider
from domains.llm.provider.domain import runtime_configuration
import domains.agent.domain.aggregates.chat_model as chat_model
from domains.agent.domain.aggregates import message_formatter

logger = logging.getLogger(__name__)

class CustomProvider(openai_provider.OpenAIProvider):


    def __init__(self, config: runtime_configuration.LLMRuntimeConfiguration):
        super().__init__(config)


    def create_model(
        self, token_counter: object|None = None, agent_name: str|None = None, **kwargs:object
    ) -> chat_model.ChatModel:
        return super().create_model(token_counter=token_counter, agent_name=agent_name, **kwargs)


    def create_formatter(
        self,
        tool_args_compression_limit: int=-1,**kwargs:object
    )-> message_formatter.MessageFormatter:
        return super().create_formatter(tool_args_compression_limit=tool_args_compression_limit, **kwargs)

    def validate_config(self, config: runtime_configuration.LLMRuntimeConfiguration)->bool:
        if not config.base_url:
            return False
        if not config.model_name:
            return False
        return True

    def get_provider_name(self) -> str:
        return "custom"