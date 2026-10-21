import importlib
import logging
import sys
import asyncio
import httpx
import os
import domains.observability.infrastructure.observers.runtime_observer_bridge as runtime_observer_bridge
import domains.llm.provider.domain.streaming_policy as streaming_policy
import domains.agent.domain.aggregates.runtime_observer as runtime_observer
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.llm.provider.application.formatting.compatible_thinking as openai_compatible_thinking

def _alias_legacy_top_level_packages() -> None:
    """把历史写法里的顶层 ``service`` / ``provider`` 指向真实包。

    本仓早期把 ``runtime.service`` / ``llm.provider`` 也写成顶层 ``service`` /
    ``provider``，而顶层包**并不存在**——下面依赖的 ``foundation`` 包里就写着
    ``from service import message_formatter``，本模块自己也写着
    ``from provider.application import message_formatters``。这类旧路径散落在依赖
    链深处，不改的话本模块（以及整条 provider 链）连 import 都过不去。

    只在旧名字**尚未**被占用时补挂，并且直接指向真实包对象、不改任何行为；等仓库
    把旧路径全改成真实路径后，本函数自然变成空操作。
    """
    for legacy_name, real_name in (("service", "runtime.service"), ("provider", "llm.provider")):
        if legacy_name in sys.modules:
            continue
        try:
            module = importlib.import_module(real_name)
        except Exception:  # noqa: BLE001 - 真实包都导不进来时维持原样，让它正常报错
            continue
        sys.modules[legacy_name] = module


_alias_legacy_top_level_packages()

import domains.llm.provider.infrastructure.foundation.adapter.foundation as provider_adapter_base
import domains.llm.provider.infrastructure.protocols.openai_compatible.retry_policy as retry_policy
import domains.llm.provider.application.formatting.chat_formatters as provider_chat_formatters
import domains.llm.provider.application.formatting.message_formatters as message_formatters
from domains.llm.provider.domain import runtime_configuration
import domains.llm.provider.application.model.capability_resolution as model_capability_resolution
import domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy as openai_protocol_strategy
import shared.network.proxy_environment as proxy_env
import domains.llm.provider.domain.openai_api_protocol as openai_api_protocol
import domains.llm.provider.infrastructure.protocols.openai_compatible.response_validation as response_validation
import domains.agent.domain.value_objects.message as message
logger = logging.getLogger(__name__)


def _optional(module: object, name: str) -> object | None:
    """从模块上取名字；取不到给 ``None``。

    ``retry_policy`` 目前是 0 字节，``provider_chat_formatters`` 里也没有
    ``TruncationgOpenaiChatFormatter``（真实类名是 ``TruncatingOpenAIChatFormatter``，
    这里是拼错的旧名）。直接取属性会让本模块在 **import 期** 就 AttributeError，
    整条 provider 链跟着废掉。退化成 ``None`` 后由 ``_resolve_protocol_strategy``
    走兜底分支，真身落地即刻生效。
    """
    return getattr(module, name, None)


NativeOpenAiChatModel= _optional(retry_policy,"NativeOpenAIChatModel")
DeepSeekReasoningFormatter= _optional(provider_chat_formatters,"DeepSeekReasoningFormatter")
KimitChatFormatter= _optional(provider_chat_formatters,"KimitChatFormatter")
StringContentOpenAIFormatter= _optional(provider_chat_formatters,"StringContentOpenAIFormatter")
TruncationgOpenaiChatFormatter= (
    _optional(provider_chat_formatters,"TruncationgOpenaiChatFormatter")
    or _optional(provider_chat_formatters,"TruncatingOpenAIChatFormatter")
)



class OpenAICompatibleAdapterBase(provider_adapter_base.LLMProviderAdapterBase):

    def __init__(self, config: runtime_configuration.LLMRuntimeConfiguration):
        super().__init__(config)

    @staticmethod
    def _create_kimi_k3_formatter(
        *,
        model_name:object,
        tool_args_compression_limit:int,
       )->message_formatters.MessageFormatter|None:

        if not isinstance(model_name, str) or not model_capability_resolution._is_kimi_k3_model(model_name):
            return None
        return provider_chat_formatters.KimitChatFormatter(
            tool_args_compression_limit=tool_args_compression_limit,
        )
    def _resolve_protocol_strategy(self) -> openai_protocol_strategy.OpenAIProtocolStrategy:
        return openai_protocol_strategy.resolve_openai_protocol_strategy(
            self.config.api_protocol,
            chat_completions_model_type=NativeOpenAiChatModel,
            deepseek_formatter=DeepSeekReasoningFormatter,
            string_content_formatter=StringContentOpenAIFormatter,
            kimi_formatter=KimitChatFormatter,
            default_formatter=TruncationgOpenaiChatFormatter,

        )

    def _resolve_effective_proxy_config(self)-> tuple[bool,str|None,str|None]:
        use_proxy= self.config.use_proxy is not False
        if not use_proxy:
            return False, None, None
        env_http_proxy= os.environ.get("HTTP_PROXY")
        env_https_proxy= os.environ.get("HTTPS_PROXY")
        http_proxy = self.config.http_proxy or self.config.https_proxy or env_http_proxy or env_https_proxy
        https_proxy =self.config.https_proxy or self.config.http_proxy or env_https_proxy or env_http_proxy
        return True, http_proxy,https_proxy

    def create_model(
        self,
        token_counter: object | None = None,
        agent_name: str | None = None,
        observer: runtime_observer.RuntimeObserver |None=None,
        **kwargs: object,
    ) -> chat_model.ChatModel:
        effective_use_proxy, http_proxy, https_proxy= self._resolve_effective_proxy_config()
        stream_enabled=streaming_policy.resolve_streaming_enabled(kwargs.get("stream"), self._load_user_config())
        chunk_idle_timeout= streaming_policy.resolve_chunk_idle_timeout(
            kwargs.get("chunk_idle_timeout"), self._load_user_config()
        )

        verify_ssl= self.config.verify_ssl if self.config.verify_ssl is not None else False

        if effective_use_proxy:
            if http_proxy or https_proxy:
                logger.warning("USing Proxy")
        else:
            logger.warning("USING PROXY")

        default_headers={
            "X-Title":"rabbit",
            "User-Agent":"codex_cli_rs/0.77.0 (Window 10.0.26100; x86_64) WindowTerminal"
        }

        if self.config.custom_headers:
            default_headers.update(self.config.custom_headers)

        configured_max_tokens = kwargs.get("max_tokens", self.config.max_tokens)
        max_tokens=(
            configured_max_tokens
            if (
                isinstance(configured_max_tokens, int)
                and not isinstance(configured_max_tokens, bool)
                and configured_max_tokens >0
            )
            else 32768
        )
        generate_kwargs: dict[str, object] = {"max_tokens": max_tokens}

        extra_model_kwrags= self._apply_model_request_strategy(
            generate_kwargs=generate_kwargs,
            base_url = self.config.base_url,
            temperature_override=kwargs.get("temperature"),
            temperature_override_provided="temperature" in kwargs,
            raw_extra_body=kwargs.get("extra_body"),
            inclde_standard_reasoning_effort =True,
        )

        configured_service_tier = self.config.service_tier
        if configured_service_tier:
            generate_kwargs["service_tier"] = configured_service_tier

        effective_api_key= self.config.api_key
        protocol_strategy = self._resolve_protocol_strategy()
        model_type= protocol_strategy.model_type
        return model_type(
            model_name=self.config.model_name,
            api_key=effective_api_key,
            base_url=self.config.base_url,
            token_counter=token_counter,
            agent_name=agent_name or "",
            http_proxy=http_proxy,
            https_proxy=https_proxy,
            trust_env=effective_use_proxy,
            stream=stream_enabled,
            ssl_verify=verify_ssl,
            default_headers=default_headers,
            image_request= kwargs.get("image_request",False),
            llm_provider= self.get_provider_name(),
            observer=observer if observer is not None else runtime_observer_bridge.InfraRuntimeObserver(),
            generate_kwargs=generate_kwargs,
            chunk_idle_timeout=chunk_idle_timeout,
            **extra_model_kwrags,
        )

    def create_formatter(
        self,
        tool_args_compression_limit: int = -1,
        **kwargs: object,
    ) -> object:
        model_name = kwargs.get("model_name") or self.config.model_name
        protocol_strategy = self._resolve_protocol_strategy()
        return protocol_strategy.create_formatter(
            model_name=model_name,
            tool_args_compression_limit=tool_args_compression_limit,
        )

    def validate_config(
        self,
        config: runtime_configuration.LLMRuntimeConfiguration,
    ) -> bool:
        if not config.base_url:
            return False
        if not config.model_name:
            return False
        if not config.api_key:
            logger.debug(
                "provider %s has no api_key configured; compatible endpoints may still accept it",
                self.get_provider_name(),
            )
        return True

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------

    async def test_connection(
        self,
        test_message: str = "Hello! Please respond with 'Connection successful'.",
        max_tokens: int = 50,
        timeout: float = 20.0,
    ) -> dict:
        if self.config.api_protocol == openai_api_protocol.OpenAIAPIProtocol.RESPONSES:
            return await self._test_responses_connection(test_message, max_tokens, timeout)
        try:
            base_url = self.config.base_url
            if not base_url:
                return {"success": False,
                        "message": "base_url is required",
                        "response": "",
                        "error":"Configuration error: base_url is not set"
                        }
            base_url = base_url.rstrip("/")
            url=f"{base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "X-Title":"rabbit",
                "Use-Agent":"codex_cli_rs/0.77.0 (Window 10.0.26100; x86_64) WindowTerminal",
            }
            if self.config.custom_headers:
                headers.update(self.config.custom_headers)
            effective_api_key = self.config.api_key
            if effective_api_key:
                headers["Authorization"] = f"Bearer {effective_api_key}"
            stream_enabled= streaming_policy.resolve_streaming_enabled(None, self._load_user_config())
            if stream_enabled:
                headers["Accept"]="text/event-stream"
            body={
                "model":self.config.model_name,
                "message":[{"role":"user","content":test_message}],
                "max_tokens":max_tokens,
                "stream":stream_enabled,
            }
            effective_use_proxy,http_proxy, https_proxy = self._resolve_effective_proxy_config()
            proxy=https_proxy if url.lower().startswith("https://") else http_proxy
            if proxy:
                logger.info(f"using proxy {proxy}")
            verify_ssl =self.config.verify_ssl if self.config.verify_ssl is not None else False
            client_kwargs :dict[str, object] = {
                "verify": verify_ssl,
                "timeout": httpx.Timeout(timeout),
            }
            client_kwargs.update(httpx.compat.build_proxy_kwargs(
                proxy_url=proxy,
                trust_env=effective_use_proxy,
            ))
            async with httpx.AsyncClient(**client_kwargs) as client:
                response= await client.post(url, json=body, headers=headers)
                if response.status_code == 200:
                    if stream_enabled:
                        content= self._parse_sse_reponse(response.text)
                    else:
                        content = response_validation.parse_json_context(response.json())
                    return {
                        "success": True,
                        "message":f"Connection successful! Model responeed: {content[:100]}",
                        "response":content,
                        "error":"",
                    }
                else:
                    error_msg= self._format_text_error(response)
                    return {
                            "success": False,
                             "message": f"Connection failed! HTTP: {response.status_code}",
                             "response": "",
                               "error":error_msg,
                               "status_code":response.status_code,
                             }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": "Connection timeout",
                "response": "",
                "error":f"Request time out after {timeout} seconds.",
            }
        except httpx.ConnectError as e:
            return {
                "success": False,
                "message": "Connection failed   ",
                "response": "",
                "error":f"Failed to connect to server:{str(e)} .",
            }
        except Exception as e:
            return {
                "success": False,
                "message": "Connection test failed   ",
                "response": "",
                "error":f"Unexpected error:{str(e)} .",
            }

    async def _test_responses_connection(
        self,
        test_message: str ,
        max_tokens: int,
        timeout: float,
    ) -> runtime_configuration.JsonObject:
        model=self.create_model(
            max_tokens=max_tokens,
            observer=runtime_observer.NoOpObserver()
        )
        response_parts:list[str]=[]
        try:
             async with asyncio.timeout(timeout):
                 response_stream= await model(
                     [
                         {
                             "type":"message",
                             "role":"user",
                             "content":[{"type":"input_text","text":test_message}],
                         }
                     ]
                 )
                 async for chunk in response_stream:
                     if isinstance(chunk, str):
                         response_parts.append(chunk.content)
                     else:
                         response_parts.extend(
                             block.text for block in chunk.content if isinstance(block, message.TextBlock)
                         )
             content="".join(response_parts)
             if not content:
                 return {
                     "success": False,
                     "message": "Empty response",
                     "response": "",
                     "error":"OpenAI Response returned no text content"
                 }
             return {
                 "success": True,
                 "message": f"Connection successful! Model responeed: {content[:100]}",
                 "response":content,
                 "error": ""
             }
        except TimeoutError:
            return {
                "success": False,
                "message": "Connection timeout",
                "response": "",
                "error": f"Request time out after {timeout} seconds.",
            }
        except Exception as e:
            return {
                "success": False,
                "message": "Connection failed   ",
                "response": "",
                "error": type(e).__name__,
            }

    def _formate_test_error(
        self,
        response,
    ) -> str:
       try:
           data = response.json()
           if "error" in data:
               error_obj= data["error"]
               if isinstance(error_obj, dict):
                   error_type= error_obj.get("type","unknown")
                   error_msg= error_obj.get("message",str(error_obj))
                   return str(error_obj)
               return response.text[:500]
       except Exception as e:
           text=response.text or ""
           if "<html" in text.lower():
               return f"HTTP {response.status_code}:  erver returned an HTTL error page instead of JSON"
           return text[:500] or f"HTTP {response.status_code}:  erver returned an HTTP error page instead of JSON"

    def _inject_proxy_env_vars(self) -> None:
        """把生效配置里的代理写进 ``os.environ``。

        返回 ``(overlay, snapshot)``：``overlay`` 是这次写了哪些键，``snapshot`` 是
        这些键**写入前**的旧值（原本不存在就不出现）。调用方在 ``finally`` 里照这两
        个字典还原即可——**不要**全局改了不收回，同进程里别的 provider 可能根本不该
        走代理。还原用 :func:`_restore_proxy_env_vars`。

        不开代理时什么都不做，返回两个空字典。
        """
        config = self._load_user_config()
        if config:
            overlay, _cleanup_keys = proxy_env.ProxyEnvBuilder.build_overlay(
            use_proxy=True,
            http_proxy=config.get("httpProxy") or config.get("http_proxy"),
            https_proxy=config.get("httpsProxy") or config.get("https_proxy"),
            proxy_username=config.get("proxyUsername") or config.get("proxy_username"),
            proxy_password=config.get("proxyPassword") or config.get("proxy_password"),
            no_proxy=config.get("noProxy") or config.get("no_proxy"),
            current_env=os.environ,
        )
        os.environ.update(overlay)

    def _apply_model_request_strategy(
        self,
        *,
        generate_kwargs: dict[str, object],
        base_url: str|None,
        temperature_override: object|None,
        temperature_override_provided: bool,
        raw_extra_body: object|None,
        inclde_standard_reasoning_effort: bool=False,
    ) -> dict[str, object]:

        temperature= openai_compatible_thinking.resolve_temperature(
            self.config.temperature, temperature_override, override_provided=temperature_override_provided,
        )
        request_options= openai_compatible_thinking.resolve_openai_compatible_request(
            model_name=self.config.model_name,
            base_url=base_url,
            thinking_mode= self.config.thinking_mode,
            reasoning_effort= self.config.reasoning_effort,
            temperature= temperature,
            raw_extra_body=raw_extra_body,
            include_standard_reasoning_effort=inclde_standard_reasoning_effort,
        )
        if request_options.temperature is not None:
            generate_kwargs["temperature"] = request_options.temperature
        elif temperature is not None:
            logger.info("skipping temperature request")

        if request_options.extra_body is not None:
            generate_kwargs["extra_body"] = request_options.extra_body
        if request_options.thinking_mode is not None:
            logger.info("skipping thinking mode")

        sdk_model_kwargs: dict[str, object] = {}
        if request_options.sdk_reasoning_effort is not None:
            sdk_model_kwargs["reasoning_effort"] = request_options.sdk_reasoning_effort
        return sdk_model_kwargs





