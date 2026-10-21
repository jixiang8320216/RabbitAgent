import os
import asyncio
import httpx
try:
    import anthropic
except ImportError:
    # Anthropic SDK 仅在真正使用 Anthropic provider 时才需要；未安装不应阻塞启动。
    anthropic = None  # type: ignore[assignment]

import domains.observability.infrastructure.observers.runtime_observer_bridge as runtime_observer_bridge
import shared.network.proxy_environment as proxy_env
import domains.llm.provider.infrastructure.foundation.adapter.foundation as provider_adapter_base
from domains.llm.provider.domain import runtime_configuration
import domains.llm.provider.infrastructure.families.anthropic.chat_model as native_anthropic_chat_model
import domains.llm.provider.domain.streaming_policy as streaming_policy
import domains.agent.domain.value_objects.message as message_module
import domains.llm.provider.application.formatting.chat_formatters as provider_chat_formatters
import domains.llm.provider.infrastructure.families.anthropic.contracts as native_transport
import domains.llm.provider.domain.streaming_policy as stream_policy
import domains.llm.provider.infrastructure.foundation.chat_transport.diagnostics as transport_diagnostics
_DEFAULT_USER_AGENT="claude-cli/2.1.112(external,cli)"

_DEFAULT_ANTHROPIC_BETA=",".join(
    [
        "claude-code-20250219",
        "context-1m-2025-08-07",
        "interleaved-thinking-2025-05-14",
        "redact-thinking-2026-02-12",
        "context-management-2025-06-27",
        "prompt-caching-scope-2026-01-05",
        "effort-2025-11-24",
    ]
)

class _AnthropicProbeResponseError(ValueError):
    ...

NativeAnthropicChatModel= native_anthropic_chat_model.NativeAnthropicChatModel

class AnthropicProvider(provider_adapter_base.LLMProviderAdapterBase):


    def __init__(self, config: runtime_configuration.LLMRuntimeConfiguration):
        super().__init__(config)


    def create_model(
        self,
        token_counter:object|None = None,
        agent_name:str|None = None,
        **kwargs:object
    )->NativeAnthropicChatModel:

       stream_enabled= streaming_policy.resolve_streaming_enabled(kwargs.get("stream"), self._load_user_config())

       effective_use_proxy= self.config.use_proxy
       if effective_use_proxy is None:
           effective_use_proxy= False

       env_http_proxy=os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
       http_proxy=None
       https_proxy=None

       # 不开代理时也要能造模型：这些是构造 ChatModel 的公共入参，
       # 下面开了代理会被同一套口径覆盖一次（幂等）。
       verify_ssl = self.config.verify_ssl if self.config.verify_ssl is not None else False
       model_name = self.config.model_name or ""
       effective_base_url = (self.config.base_url or "").strip().rstrip("/") or None
       max_tokens = self.config.max_tokens if self.config.max_tokens and self.config.max_tokens >0 else 64000
       generate_kwargs:dict[str,object] ={"max_tokens": max_tokens}
       temperature = kwargs.get("temperature",self.config.temperature)
       if temperature is not None:
           generate_kwargs["temperature"] = temperature

       if effective_use_proxy:
           http_proxy= self.config.http_proxy or self.config.https_proxy or env_http_proxy
           https_proxy = self.config.https_proxy or self.config.http_proxy or env_http_proxy
           http_proxy = proxy_env.ProxyEnvBuilder.inject_proxy_credentials(
               http_proxy,
               self.config.proxy_username,
               self.config.proxy_password,
           )
           https_proxy = proxy_env.ProxyEnvBuilder.inject_proxy_credentials(
               https_proxy,
               self.config.proxy_username,
               self.config.proxy_password,
           )
           verify_ssl = self.config.verify_ssl if self.config.verify_ssl is not None else False
           model_name = self.config.model_name or ""
           effective_base_url = (self.config.base_url or "").strip().rstrip("/") or None
           max_tokens = self.config.max_tokens  if self.config.max_tokens and self.config.max_tokens >0 else 64000
           generate_kwargs:dict[str,object] ={"max_tokens": max_tokens}
           temperature = kwargs.get("temperature",self.config.temperature)
           if temperature is not None:
               generate_kwargs["temperature"] = temperature

       provider_headers={
           "User-Agent": _DEFAULT_USER_AGENT,
           "x-app":"cli",
           "anthropic-beta":_DEFAULT_ANTHROPIC_BETA,
           "anthropic-dangerous-direct-browser-access":"true",
           "accept-language":"*",
           "sec-fetch-dest":"cors",
       }

       if self.config.custom_headers:
           provider_headers.update(self.config.custom_headers)

       return NativeAnthropicChatModel(
           model_name=model_name,
           api_key= self.config.api_key,
           base_url = effective_base_url,
           token_counter = token_counter,
           agent_name = agent_name or "",
           stream= stream_enabled,
           http_proxy = http_proxy,
           https_proxy = https_proxy,
           ssl_verify = verify_ssl,
           default_headers = provider_headers,
           observer = runtime_observer_bridge.InfraRuntimeObserver(),
            llm_provider = self.get_provider_name(),
            generate_kwargs = generate_kwargs,
       )

    # ------------------------------------------------------------------
    # provider 名
    # ------------------------------------------------------------------

    def get_provider_name(self)->str:
        return "anthropic"

    # ------------------------------------------------------------------
    # 请求格式化器
    # ------------------------------------------------------------------

    def create_formatter(
        self,
        tool_args_compression_limit:int=-1,
        **kwargs:object,
    )->object:
       return provider_chat_formatters.TruncatingOpenAIChatFormatter(
           tool_args_compression_limit = tool_args_compression_limit,
       )

    # ------------------------------------------------------------------
    # 配置校验
    # ------------------------------------------------------------------

    def validate_config(
        self,
        config:runtime_configuration.LLMRuntimeConfiguration,
    )->bool:
        if config.model_name is None:
            return False
        if not config.api_key:
            return False
        return  True

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------

    async def test_connection(
        self,
        test_message:str="Hello! Please respond with 'Connection success'.",
        max_tokens:int=50,
        timeout:float=20.0,
    )->runtime_configuration.JsonObject:
        stream_enabled = stream_policy.resolve_streaming_enabled(None,self._load_user_config())
        try:
            async with asyncio.timeout(timeout):# type: ignore[arg-type]
                response = await self._send_connection_probe(
                    text_message= test_message,
                    max_tokens=max_tokens,
                    stream_enabled = stream_enabled,
                )
        except (TimeoutError, anthropic.APITimeoutError, httpx.TimeoutException) :
            return self._connection_failure(
                "Connection timeout",
                f"Request timed out after {timeout:g} seconds",
                408,
            )
        except Exception as ex:
            return self._connection_exception_result(ex)
        return {
            "success": True,
            "message": f"Connection successful! Anthroic responded: {response[:100]} ",
            "response":response,
            "error":"",
            "model":self.config.model_name or "",
        }

    async def _send_connection_probe(
        self,
            *,
        test_message:str,
        max_tokens:int=50,
        stream_enabled:bool,
    )->str:
        model = self.create_model(
            agent_name="connection_probe",
            stream=stream_enabled,
        )
        client = model._create_anthropic_client()
        params={
            "model":self.config.model_name or "",
            "messages":[{"role":"user", "content":test_message}],
            "max_tokens":max_tokens,
        }

        content_parts:list[str]=[]
        try:
            if stream_enabled:
                async with client.beta.messages.stream(**params) as stream:
                    async for event in stream:
                        projected= model._anthropic_event_to_chat_response(event)
                        content_parts.extend(self._connection_content(projected))
            else:
                raw_response = await client.beta.messages.create(**params)
                projected= model._anthropic_event_to_chat_response(raw_response)
                content_parts.extend(self._connection_content(projected))
        finally:
            await client.close()
        if not content_parts:
            raise _AnthropicProbeResponseError(
                "Provider returned no consumable text, thinking, or tool call content"
            )
        return "".join(content_parts)

    def _connection_content(self, response:object)->str|None:
        if response is None:
            return []
        parts:list[str]=[]
        for block in response.content:
            if isinstance(block, message_module.TextBlock) and block.text:
                parts.append(block.text)
            elif isinstance(block, message_module.ThinkingBlock) and block.thinking:
                parts.append(block.thinking)
            elif isinstance(block, message_module.ToolUseBlock):
                parts.append(f"Tool call :{block.name or '(unnamed)'}")
        return parts

    def _connection_exception_result(
        self,
        error:BaseException,
    )->runtime_configuration.JsonObject:
        """把一个异常翻成失败结果字典。"""
        status_code = self._exception_status_code(error)
        if status_code is not None:
             return self._connection_failure(
                         "Connection failed",
                        self._status_error_diagnostic(error),
                        status_code,
             )
        if self._exception_matches(error,(anthropic.APIConnectionError,httpx.TransportError)):
            return self._connection_failure("Connection failed",
                        "Could not reach the provider endpoint",
                        502,
        )
        if isinstance(error, _AnthropicProbeResponseError):
            return self._connection_failure("Connection failed",str(error), 502)
        return self._connection_failure("Connection test failed", "Provider returned an invalid Anthropic response",502)

    @classmethod
    def _status_error_diagnostic(cls, error:BaseException)->str:
        for candidate in cls._exception_chain(error):
            response= native_transport._provider_error_response(candidate)
            if response is None:
                continue
            try:
                diagnostic=transport_diagnostics.safe_provider_diagnostic(response.json())
            except (ValueError, httpx.ResponseNotRead) :
                diagnostic=""
            if diagnostic:
                return diagnostic
        return "Provider request wa rejected"


    @staticmethod
    def _connection_failure(
        message:str,
        error:str,
        status_code:int,
    )->runtime_configuration.JsonObject:
        return {
            "success":False,
            "message":message,
            "response":"",
            "error":error,
            "status_code":status_code,
        }

    @classmethod
    def _exception_status_code(cls, error:BaseException)->int|None:
        for candidate in cls._exception_chain(error):
            status_code=native_transport._provider_error_status(candidate)
            if isinstance(status_code, int) and 400<=status_code<=599:
                return status_code
        return None

    @classmethod
    def _exception_matches(cls, error:BaseException, expected: tuple[type[BaseException],...])->bool:
        return any(isinstance(candidate, expected) for candidate in cls._exception_chain(error))

    @staticmethod
    def _exception_chain(error:BaseException)->tuple[BaseException,...]:
        chain:list[BaseException]=[]
        current: BaseException|None=error
        visited:set[int]=set()
        while current is not None and id(current) not in visited:
            chain.append(current)
            visited.add(id(current))
            current=current.__cause__ or current.__context__
        return tuple(chain)

    def _inject_proxy_env_vars(self)->tuple[dict[str,str], dict[str,str]]:
        """把配置里的代理写进 ``os.environ``。

        返回 ``(overlay, snapshot)``：``overlay`` 是这次写了哪些键，``snapshot``
        是写入前这些键的旧值（键原本不存在就不出现在里面）。调用方在 ``finally``
        里照这两个字典还原即可，**不要**全局改了不收回——同进程里其它 provider
        可能根本不该走代理。

        不开代理（``use_proxy`` 为假）时什么都不做，返回两个空字典。
        """
        config = self._load_user_config()
        if config:
            use_proxy = config.get("use_proxy",False)
            if use_proxy:
                overlay,_cleanup_keys =proxy_env.ProxyEnvBuilder.build_overlay(
                    use_proxy=True,
                    http_proxy=config.get("http_proxy") or config.get("httpProxy") ,
                    https_proxy=config.get("https_proxy") or config.get("httpsProxy") ,
                    proxy_password= config.get("proxy_password") or config.get("proxyPassword") ,
                    proxy_username= config.get("proxy_username") or config.get("proxyUsername") ,
                    no_proxy= config.get("no_proxy") or config.get("noProxy") ,
                    current_env= os.environ,
                )
                os.environ.update(overlay)















