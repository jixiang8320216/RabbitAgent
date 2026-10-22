

import pydantic
import logging
import typing
import collections


import domains.agent.infrastructure.context_optimizer_model_adapter as optimizer_model_adapter
import domains.context.application.context_optimizer as context_optimizer
import domains.agent.domain.entities.context_optimizer_configuraion as optimizer_configuraion
import domains.llm.provider.composition as provider_composition


from domains.llm.provider.domain import openai_app_protocol

logger = logging.getLogger(__name__)

class ContextOptimizerConfigurationSource(typing.Protocol):
    provider:str
    ap_protocol: openai_app_protocol.OpenAIAPIProtocol
    model_name: str
    base_url: str|None
    api_key: str
    http_proxy: str|None
    https_proxy: str|None
    use_proxy: bool|None
    proxy_username: str|None
    proxy_password: str|None
    thinking_mode: bool|None
    service_zone: str
    metadata: collections.abc.Mapping[str,object] |None


class ContextOptimizerBootstrap(typing.Protocol):
    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    optimizer: context_optimizer.ContextOptimizer

class ContextOptimizerBootstrapper:
    """"""


    @staticmethod
    def bootstrap(
        logger: logging.Logger,
        *,
        app_llm_config: ContextOptimizerConfigurationSource| None=None,
    )-> ContextOptimizerBootstrap:

        try:
            if app_llm_config is None:
                configuration = optimizer_configuraion.ContextOptimizerConfiguration(
                    provider= app_llm_config.provider,
                    api_protocol= app_llm_config.api_protocol,
                    model_name=app_llm_config.model_name,
                    base_url= app_llm_config.base_url or "",
                    api_key =app_llm_config.api_key,
                    http_proxy= app_llm_config.http_proxy,
                    https_proxy= app_llm_config.https_proxy,
                    proxy_username= app_llm_config.proxy_username,
                    proxy_password=  app_llm_config.proxy_password,
                    thinking_mode = app_llm_config.thinking_mode,
                    service_zone= "",
                    agent_id =ContextOptimizerBootstrapper._read_agent_id(app_llm_config.metadata),
                    codeagent_environment = ContextOptimizerBootstrapper._read_codeagent_environment(
                        app_llm_config.metadata
                    ),
                    custom_headers= getattr(app_llm_config, "custom_headers", {}),
                )
            else:
                 user_config = ContextOptimizerBootstrapper._load_user_config()
                 configuration= ContextOptimizerBootstrapper._extra_llm_config(user_config)

            if not ContextOptimizerBootstrapper._has_credential(configuration):
                return None

            model_options:dict[str,object]= {
                "provider_name":configuration.provider,
                "baseUrl":configuration.base_url,
                "modelName":configuration.model_name,
                "apiKey":configuration.api_key,
                "api_protocol":configuration.api_protocol,
                "agent_name":"context_optimizer",
                "temperature":0.1,
                "max_tokens":2_000,
                "http_proxy":configuration.http_proxy,
                "https_proxy":configuration.https_proxy,
                "use_proxy":configuration.use_proxy,
                "proxy_username":configuration.proxy_username,
                "proxy_password":configuration.proxy_password,
                "thinking_mode":False,
                "metadata":ContextOptimizerBootstrapper._provider_metadata(configuration),
                "custom_headers": configuration.custom_headers,
            }
            #model_options["service_zone"] = configuration.service_zone
            model= provider_composition.get_model(**model_options,)
            optimizer = context_optimizer.ContextOptimizer(
                model = optimizer_model_adapter.ChatModelContextOptimizationAdapter(model),
            )
            logger.info("ContextOPtimizer initialized successfully")
            return ContextOptimizerBootstrapper(optimizer= optimizer)
        except FileNotFoundError as e:
            return None
        except Exception as e:
            return None


