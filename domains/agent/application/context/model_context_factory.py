import typing
import logging

import domains.llm.provider.application.token.token_counter as token_counter_module
import domains.llm.provider.composition as provider_composition
import domains.agent.domain.aggregates.chat_model as chart_model
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.domain.aggregates.message_formatter as message_formatter

logger = logging.getLogger(__name__)

class FormatterConfigurationSource(typing.Protocol):

    def get_property(self,
        key: str,
        default: object|None = None,
        expert_type: type[object]|None = None,
     ) -> object | None: ...

class ModelContextFactory:
    """模型上下文工厂。

    负责持有「构造模型上下文所需的两样东西」：

    - ``configmanager``：配置管理器（沿用调用点既有拼写，非 ``config_manager``）。
    - ``token_counter``：token 计数器，由外部注入；为 ``None`` 时表示尚未装配。

    装配位置：``AgentFactory.__init__``。两者均可缺省——缺哪一项都只是对应的
    能力不可用，不应让工厂本身构造失败。
    """

    def __init__(
        self,
        *,
        configmanager: FormatterConfigurationSource |None = None,
        token_counter: token_counter_module.TokenCounter|None = None,
    ) -> None:
        self.configmanager = configmanager
        self.token_counter = token_counter if token_counter is not None else token_counter_module.TokenCounter()

    def get_token_counter(self) -> token_counter_module.TokenCounter:
        return self.token_counter

    def create_model(
        self,
        llm_config: agent_runtime_module.AgentRuntimeModelConfig,
        agent_name:str,
        enable_token_tracking:bool=True,
    )->chart_model.ChatModel:
        model_kwargs={
            "temperature":llm_config.temperature,
            "max_tokens":llm_config.max_tokens,
            "reasoning_effort":llm_config.reasoning_effort,
            "service_tier":llm_config.service_tier,
            "http_proxy":llm_config.http_proxy,
            "https_proxy":llm_config.https_proxy,
            "use_proxy":llm_config.use_proxy,
            "proxy_username":llm_config.proxy_username,
            "proxy_password": llm_config.proxy_password,
            "thinking_mode":llm_config.thinking_mode,
            "api_protocol":llm_config.api_protocol,
            "custom_headers":llm_config.custom_headers,
        }

        if llm_config.metadata is not None:
            model_kwargs["metadata"] = llm_config.metadata
        if enable_token_tracking:
            model=provider_composition.get_model(
                llm_config.provider,
                llm_config.base_url,
                llm_config.model_name,
                llm_config.api_key,
                token_counter=self.token_counter,
                agent_name=agent_name,
                **model_kwargs
            )
        else:
            model=provider_composition.get_model(
                llm_config.provider,
                llm_config.base_url,
                llm_config.model_name,
                llm_config.api_key,
                token_counter=None,
                agent_name=None,
                **model_kwargs
            )
        return model

    def create_formatter(self,
        llm_config: agent_runtime_module.AgentRuntimeModelConfig,
        tool_args_compression_limit:int=-1,
        **extra: object,
    )->message_formatter.MessageFormatter:
        """造请求格式化器。

        ``extra`` 收下调用点额外传的 ``agent_name`` / ``enable_token_tracking``：
        格式化器此刻用不到它们，但**不能**因为多了两个关键字就让装配失败。
        """
        if tool_args_compression_limit != -1 and self.configmanager is not None:
            configured_limit = self.configmanager.get_property(
                "tool_args_compression_limit",
                -1,
                expert_type=int,
            )
            if not isinstance(configured_limit, int) or isinstance(configured_limit, bool):
                raise TypeError("tool_args_compression_limit must be an integer")
        return provider_composition.get_formatter(
            llm_config.provider,
            tool_args_compression_limit= tool_args_compression_limit,
            model_name=llm_config.model_name,
            api_protocol=llm_config.api_protocol,
        )


















