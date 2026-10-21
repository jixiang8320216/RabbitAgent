import asyncio
import secrets
import threading
import typing
import logging
try:
    import anthropic
except ImportError:
    # Anthropic SDK 仅在真正使用 Anthropic provider 时才需要；未安装不应阻塞启动。
    anthropic = None  # type: ignore[assignment]

import httpx

import domains.llm.provider.infrastructure.foundation.chat_transport.provider_chat_model as shared_chat_model
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.interruption as runtime_interruption
import domains.llm.provider.infrastructure.families.anthropic.request as anthropic_request
import domains.llm.provider.infrastructure.families.anthropic.response as anthropic_response
import domains.llm.provider.infrastructure.families.anthropic.contracts as native_transport
import shared.network.http_client_compatibility as httpx_compat
import domains.agent.domain.value_objects.chat_response as chat_contracts
import domains.llm.provider.infrastructure.families.anthropic.messages as anthropic_messages
logger = logging.getLogger(__name__)

#: 生产线程往队列里塞的「流已结束」哨兵，和 text chunk（一定是 str）不会撞。
_STREAM_END: object = object()


def _to_openai_messages(
    messages: list[chat_model.JsonObject] | None,
) -> list[dict[str, str]]:
    """把 runtime 侧的消息压成 ``LLM.think`` 认的 ``[{"role", "content"}]``。

    ``content`` 可能是纯文本、也可能是文本块列表（``{"type": "text", ...}``），
    ``LLM.think`` 只接受字符串，所以列表在这里摊平成多行文本。
    """
    prompt: list[dict[str, str]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role is None:
            continue
        content = message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or block.get("thinking")
                    if text:
                        parts.append(str(text))
                elif block is not None:
                    parts.append(str(block))
            content = "\n".join(parts)
        elif content is None:
            content = ""
        prompt.append(
            {
                "role": str(role),
                "content": content if isinstance(content, str) else str(content),
            }
        )
    return prompt


async def _iter_sync_stream(
    factory: typing.Callable[[], typing.Iterator[object]],
) -> typing.AsyncGenerator[object, None]:
    """把**同步生成器**挪到 worker 线程迭代，chunk 逐个回流事件循环。

    ``asyncio.to_thread`` 只能包住「一次调用」，包不住「一个生成器」：直接
    ``to_thread(factory)`` 得到的只是生成器对象（函数体没跑），在事件循环里
    ``for`` 它照样会同步阻塞。所以这里用一个线程跑**完整的迭代**，再用
    ``asyncio.Queue`` 把 chunk 送回主线程——``LLM.think`` 的流式 HTTP 读取
    全部发生在那个线程里。

    消费者提前退出（``aclose()`` / 抛错）时置 ``stopped``，生产者在下一个
    chunk 之后自然结束，不会留下一个还在读流的线程。
    """
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[object]" = asyncio.Queue()
    stopped = threading.Event()

    def _produce() -> None:
        try:
            for item in factory():
                if stopped.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except BaseException as exc:
            # 生成器内部的异常原样交给消费者，别让它死在另一个线程里。
            if not stopped.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            if not stopped.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, _STREAM_END)

    worker = asyncio.ensure_future(asyncio.to_thread(_produce))
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_END:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stopped.set()
        if worker.done():
            try:
                worker.result()
            except BaseException:
                pass


class NativeAnthropicChatModel(shared_chat_model.ProviderChatModelBase):

    _STOP_REASON_MAP= {
        "end_turn":"stop",
        "max_tokens":"length",
        "model_context_window_exceeded":"length",
        "tool_use":"tool_calls",
        "stop_sequence":"stop",
        "pause_turn":"pause",
        "refusal":"refusal",
        "compaction":"compaction",
    }

    _device_id_cached: str|None=None
    _prev_cache_read_tokens: int|None=None

    @classmethod
    def _map_stop_reason(cls, stop_reason: typing.Optional[str]) -> typing.Optional[str]:
        return anthropic_response.map_stop_reason(stop_reason,cls._STOP_REASON_MAP)
    @staticmethod
    def _stop_details_from_sdk(stop_details:object)-> chat_contracts.ChatStopDetails|None:
        return anthropic_response.stop_details_from_sdk(stop_details)

    def _cc_device_id(self)->str:
        did=self._device_id_cached
        if did is None:
            did=secrets.token_hex(32)
            self._device_id_cached = did
        return did

    def _create_anthropic_client(self, request_headers:dict[str,str]|None=None):
        proxy_url =self._https_proxy or self._http_proxy
        base_client_kwargs={
            "verify": self.ssl_verify,
            "timeout": httpx.Timeout(600.0, connect=30.0),
        }

        base_client_kwargs.update(
            httpx_compat.build_proxy_kwargs(
                proxy_url=proxy_url,
                trust_env= False if not proxy_url else True,
            )
        )
        http_client = httpx.Client(**base_client_kwargs)
        headers= dict(request_headers) if request_headers is not None else dict(self._default_headers or {})
        effective_api_key = self._auth_api_key or self.api_key
        if effective_api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {effective_api_key}"
        if "anthropic-version" not in headers:
            headers["anthropic-version"]="2023-06-01"
        return anthropic.AsyncAnthropic(
            api_key= effective_api_key or "dummy",
            base_url= self.base_url,
            http_client=http_client,
            max_retries=0,
            default_headers=headers,
        )

    PROMPT_SECTION_SEPARATOR=("\n\n---\n\n")
    @staticmethod
    def _split_messages(
        messages: list[chat_model.JsonObject],
    )->tuple[str|None, list[native_transport.ProviderMessage]]:
       return anthropic_messages.split_messages(messages)

    _CACHE_BREAK_REL_THREAHOLD=0.05
    _CACHE_BREAK_ABS_THREAHOLD=2000

    def _detect_cache_break(self, input_tokens:int, cache_read:int)->None:
        prev=self._prev_cache_read_tokens
        if prev is not None and prev >0:
            drop=prev-cache_read
            if drop>self._CACHE_BREAK_ABS_THREAHOLD and drop/prev>self._CACHE_BREAK_REL_THREAHOLD:
                logger.warning("agent=%s model=%s cache read %d. Prefix likely changed.", self.agent_name, self.model_name, prev)
        self._prev_cache_read_tokens=cache_read

    _NON_CACHEABLE_BLOCK_TYPES = frozenset({"thinking","redacted_thinking"})

    @classmethod
    def _mark_last_message_cacheable(
        cls,
        api_messages: list[native_transport.ProviderMessage],
    )->None:
        anthropic_messages.mark_last_message_cacheable(api_messages, excluded_block_types= cls._NON_CACHEABLE_BLOCK_TYPES,)

    _DYNAMIC_SECTION_MARKERS=(
        "# Current Runtime Environment",
        "# Skill Runtime Context",
        "# \U0001f5a5\ufe0f Current Runtime Environment",
        "# \U0001f5c2\ufe0f Skill Runtime Context",
    )
    @classmethod
    def _split_system_into_blocks(
        cls,
        system_text:str|None,
    )-> list[native_transport.ProviderMessage]:
        return anthropic_messages.split_system_into_blocks(
            system_text,
            separator = cls.PROMPT_SECTION_SEPARATOR,
            dynamic_markers = cls._DYNAMIC_SECTION_MARKERS,
        )
    @staticmethod
    def _stabilize_tool_order(tools: list[native_transport.ProviderMessage]|None)->list[native_transport.ProviderMessage]|None:
        return anthropic_messages.stabilize_tool_order(tools)

    @staticmethod
    def _convert_tools(tools: list[chat_model.JsonObject])->list[native_transport.ProviderMessage]:
        return anthropic_messages.convert_tools(tools)

    @staticmethod
    def _anthropic_event_to_chat_response(event:object)->native_transport.ChatResponse|None:
        return anthropic_response.event_to_chat_response(
            event,
            stop_reason_map= NativeAnthropicChatModel._STOP_REASON_MAP,
        )

    @staticmethod
    def _anthropic_message_to_chat_response(raw_message:object)->native_transport.ChatResponse:
        return anthropic_response.message_to_chat_response(
            raw_message,
            stop_reason_map= NativeAnthropicChatModel._STOP_REASON_MAP,
        )

    async def __call__(
        self,
        messages:list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject]|None=None,
        tool_choice:str|chat_model.JsonObject|None=None,
        **kwargs:object,
        )->typing.AsyncGenerator[native_transport.ChatResponse,None]:
        return await anthropic_request.AnthropicRequestExecutor(
            self,
            interruptible_sleep= runtime_interruption.interruptible_sleep,
            request_logger = logger,
        ).execute(messages, tools, tool_choice, **kwargs)
