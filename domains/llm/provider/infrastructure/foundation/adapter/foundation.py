import abc
import domains.llm.provider.domain.runtime_configuration as runtime_configuration
import domains.agent.domain.aggregates.execution_context as request_execution_context
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.message_formatter as message_formatter

class LLMProviderAdapterBase(abc.ABC):
    """"""

    family_id:str = ""

    def __init__(self, config: runtime_configuration.LLMRuntimeConfiguration):
        self.config = config

    def get_provider_name(self)->str:
        """当前 adapter 的 provider 名。

        优先取配置里显式写的 ``provider``；没有就用 ``family_id``；都没有再从
        类名推导（``AnthropicProvider`` → ``anthropic``）。
        """
        provider = getattr(self.config, "provider", None)
        if provider:
            return str(provider)
        if self.family_id:
            return self.family_id
        name = type(self).__name__
        if name.endswith("Provider"):
            name = name[: -len("Provider")]
        return name.lower() or name

    @abc.abstractmethod
    def create_model(
        self,
        token_counter:object|None = None,
        agent_name:str|None = None,
        **kwargs:object
    )->chat_model.ChatModel:...

    @abc.abstractmethod
    def create_formatter(
        self,
        tool_args_compression_limit:int=-1,
        **kwargs:object
    )->message_formatter.MessageFormatter:...

    @abc.abstractmethod
    def validate_config(
        self,
        config:runtime_configuration.LLMRuntimeConfiguration,
    )->bool:...

    @abc.abstractmethod
    def test_connection(
        self,
        test_message:str="Hello! Please respond with 'Connection successful'.'",
        max_tokens:int=50,
        timeout:float=20.0,
    )->runtime_configuration.JsonObject:...

    def _load_user_config(self)->runtime_configuration.JsonObject|None:
        merged_config = request_execution_context.get_effective_runtime_config()
        if not merged_config:
            return None
        return dict(merged_config)