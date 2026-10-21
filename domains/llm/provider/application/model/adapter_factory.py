import threading
import typing
import logging

import domains.llm.provider.application.configuration.runtime_resolution as runtime_configuration_resolution
import domains.llm.provider.application.capability.adapter_port as provider_adapter_port
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.message_formatter as message_formatter
import domains.llm.provider.domain.runtime_configuration as runtime_configuration
logger = logging.getLogger(__name__)

type ModelCacheKey = tuple[str,str|None]
type FormatterCacheKey=tuple[str,int]


class ProviderAdapterFactory(typing.Protocol):

    def create(self,
            provider_name:str,
            config: runtime_configuration.LLMRuntimeConfiguration,
        )-> provider_adapter_port.LLMProviderAdapter:
        """"""


class  LLMModelAccessService:
    """模型访问服务。

    把「拿到一个可用 LLM 所需的两个来源」收齐并持有：

    - ``configuration_source``：配置来源（``AgentFactory`` 传入的是
      ``ConfigManager``），用于读取当前生效的模型配置。
    - ``provider_factory``：provider 工厂（``composition.create_provider_factory()``
      的产出），用于按配置实例化具体的 provider。

    装配位置：``AgentFactory.__init__``。两者均可缺省——缺哪一项都只是对应的
    能力不可用，不应让服务本身构造失败。
    """

    def __init__(
        self,
        *,
        configuration_source: runtime_configuration_resolution.ConfigurationSource,
        provider_factory: ProviderAdapterFactory,
    ) -> None:
        self.configuration_source = configuration_source
        self._provider_factory = provider_factory
        self._model_cache:dict[ModelCacheKey, chat_model.ChatModel] = {}
        self._formatter_cache:dict[FormatterCacheKey, message_formatter.MessageFormatter] = {}
        self._model_cache_lock= threading.Lock()
        self._formatter_cache_lock= threading.Lock()
        self._configuration_resolver : runtime_configuration_resolution.LLMRuntimeConfigurationResolver |None= None
        self._configuration_resolver_lock: threading.Lock = threading.Lock()
