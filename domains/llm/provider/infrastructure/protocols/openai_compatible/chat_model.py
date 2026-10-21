import typing

import httpx
import openai
import logging

import domains.llm.provider.infrastructure.foundation.chat_transport.provider_chat_model as shared_chat_model
from domains.llm.provider.infrastructure.protocols.openai_compatible import response_validation
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.interruption as runtime_interruption
import shared.network.http_client_compatibility as httpx_compat
import domains.llm.provider.infrastructure.protocols.openai_compatible.projections as native_transport
import domains.llm.provider.infrastructure.protocols.openai_compatible.request as native_openai_request

logger = logging.getLogger(__name__)
class NativeOpenAiChatModel(shared_chat_model.ProviderChatModelBase):
    """OpenAI 兼容协议的 native 传输实现。

    本类补齐了三个辅助方法（构造客户端、安全预览响应体、安全预览错误）；
    真正的 ``__call__`` 请求逻辑由各 provider family 或 ``retry_policy`` 里的
    子类继续实现。
    """

    def _create_openapi_client(self, request_headers:dict[str,str]|None=None):
        """构造一个 OpenAI 兼容的异步客户端。

        把基类的凭据 / base_url / 默认头 / 代理 / SSL / trust_env 都喂给
        ``openai.AsyncOpenAI``。超时走 ``chunk_idle_timeout``，openai 自带的
        重试关掉（退避重试由 ``ProviderChatModelBase`` 自己负责）。代理与
        ``verify`` 通过共享的 ``httpx.AsyncClient`` 注入，从而同时支持
        ``https_proxy`` / ``http_proxy`` / ``ssl_verify`` / ``trust_env``。
        """
        proxy_url =self._https_proxy or self._http_proxy
        if request_headers is not None:
            final_headers = dict(request_headers)
        else:
            final_headers =dict(self._default_headers)
            if self._auth_headers:
                final_headers.update(self._auth_headers)
        final_headers["Connection"] = "close"
        effective_api_key =self._auth_api_key or self.api_key or "dummy"
        base_client_kwargs = {
            "verify":self.ssl_verify,
            "timeout":httpx.Timeout(600.0, connect=30.0),
            "follow_redirects":True,
            "limits": httpx.Limits(max_connections=1, max_keepalive_connections=0),
        }
        base_client_kwargs.update(
            httpx_compat.build_proxy_kwargs(
                proxy_url=proxy_url,
                trust_env=bool(proxy_url) if self._trust_env is None else self._trust_env,
            )
        )
        http_client = httpx.AsyncClient(**base_client_kwargs)
        return openai.AsyncOpenAI(
            api_key=effective_api_key,
            base_url=self.base_url,
            http_client=http_client,
            max_retries=0,
            default_headers=self._default_headers,
          )
    @staticmethod
    def _safe_response_body_preview(response:httpx.Response|None, limit: int = 500) -> tuple[int|None, str]:
        if response is None:
            return None, ""
        try:
            body= response.text
        except httpx.ResponseNotRead:
            body = ""
        if not body:
            try:
                body=response.content.decode("utf-8", errors="replace")
            except httpx.ResponseNotRead:
                body = ""
        return response.status_code, response_validation.safe_provider_diagnostic(body,limit=limit)

    @classmethod
    def _safe_openai_error_preview(cls, error:BaseException,  limit: int = 500) -> tuple[int|None, str]:
        status_code, preview= cls._safe_response_body_preview(native_transport._provider_error_response(error), limit=limit)
        if preview or not isinstance(error, openai.APIError):
            return status_code, preview
        return status_code, response_validation.safe_provider_diagnostic(error.body,limit=limit)

    async def __call__(
        self,
        messages:list[chat_model.JsonObject],
        tools:list[chat_model.JsonObject]|None=None,
        tool_choice: str|chat_model.JsonObject|None=None,
        **kwargs:object
    )->typing.AsyncGenerator[native_transport.ChatResponse, None]:
        return await native_openai_request.OpenAIRequestExecutor(
            self,
            interruptible_sleep = runtime_interruption.interruptible_sleep,
            request_logger = logger,
        ).execute(messages,tools,tool_choice,kwargs)

























