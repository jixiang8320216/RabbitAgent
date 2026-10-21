import asyncio
import json
import logging
import time
import typing
import uuid
import secrets
import httpx
import openai
import pydantic

import domains.agent.domain.value_objects.message as message
import domains.agent.domain.value_objects.chat_response as chat_response
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.interruption as runtime_interruption

import domains.llm.provider.infrastructure.foundation.chat_transport.provider_chat_model as shared_chat_model
import domains.llm.provider.infrastructure.protocols.openai_compatible.responses_projection as responses_projection
import shared.network.http_client_compatibility as httpx_compat
from domains.llm.provider.infrastructure.foundation.chat_transport import retry_coordinator, failure_classification
from domains.llm.provider.infrastructure.protocols.openai_compatible import responses_observation
import domains.llm.provider.infrastructure.protocols.openai_compatible.projections as native_transport
import domains.llm.provider.domain.retry_policy as retry_policy
logger = logging.getLogger(__name__)

_ChatModelBase = shared_chat_model.ProviderChatModelBase

class _ResponseStream(typing.Protocol):
    """OpenAI Responses API 流式响应的最小协议（``client.responses.create(stream=True)`` 的返回值）。

    实际是 ``openai.AsyncStream[ResponseStreamEvent]``；这里只声明我们用到
    的 ``__aiter__`` / ``aclose``，避免和具体 SDK 类型耦合。
    """

    def __aiter__(self) -> typing.AsyncIterator[object]: ...

    async def aclose(self) -> None: ...

class OpenAIResponsesModel(_ChatModelBase):

    @property
    def output_token_budget(self) -> int:
        for options in (self._init_generate_kwargs, self.model_kwargs):
            value = options.get("max_ouput_tokens", options.get("max_tokens"))
            if isinstance(value, int) and not isinstance(value, bool):
                return max(value, 0)
        return 0

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    async def __call__(
        self,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject] | None = None,
        tool_choice: str | chat_model.JsonObject | None = None,
        **kwargs: pydantic.JsonValue,
    ) -> typing.AsyncGenerator[chat_response.ChatResponse, None]:
        operation_kind_value= kwargs.get("usage_operation_kind",None) or kwargs.pop("operation_kind","normal")
        operation_kind =operation_kind_value if isinstance(operation_kind_value, str) else "normal"
        operation_id = f"{self.agent_name}_{int(time.time()*1000)}_{secrets.token_hex(4)}"
        await self._send_thinking_start(
            operation_id, messages, tools, operation_kind=operation_kind
        )
        return self._generate_observed(
            messages,
            tools,
            tool_choice,
            kwargs,
            operation_id=operation_id,
            operation_kind=operation_kind,
        )
    # ------------------------------------------------------------------
    # 可观测生成（重试 + observer 通知 + 累积用量）
    # ------------------------------------------------------------------

    async def _generate_observed(
        self,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject] | None,
        tool_choice: str|chat_model.JsonObject | None,
        kwargs: dict[str, pydantic.JsonValue],
        *,
        operation_id: str,
        operation_kind: str,
    ) -> typing.AsyncGenerator[chat_response.ChatResponse, None]:
        observation= responses_observation.OpenAIResponsesObservation(started_at=time.time())
        error_code:str|None=None
        try:
            async for chunk in self._call_with_retries(
                messages, tools, tool_choice, kwargs, operation_id=operation_id
            ):
                for thinking_delta in observation.observe(chunk):
                    try:
                        await self.observer.on_thinking_content(
                            self.agent_name,
                            operation_id= operation_id,
                            content = thinking_delta,
                        )
                    except Exception as exc:
                        native_transport._log_observer_callback_failure(
                            "on_thinking_content",
                            self.agent_name,
                            exc,
                        )
                yield chunk
        except Exception as exc:
            error_coder= retry_policy.provider_failure_diagnostic_code(exc)
            raise
        finally:
            await self._finish_observation(
                observation,
                operation_id=operation_id,
                operation_kind=operation_kind,
                error_code = error_code,
            )
    # ------------------------------------------------------------------
    # 收尾观测（记账 + thinking_end + complete）
    # ------------------------------------------------------------------

    async def _finish_observation(
        self,
        observation: responses_observation.OpenAIResponsesObservation,
        *,
        operation_id: str,
        operation_kind: str,
        error_code: str | None,
    ) -> None:
        usage= observation.usage
        try:
            self._record_tokens(
                usage.input_tokens,
                usage.output_tokens,
                usage.reasoning_tokens,
                usage.cached_tokens,
                operation_kind=operation_kind,
            )
        except Exception as exc:
            logger.warning("on_usage failed", exc_info=True)

        # 2) thinking_end（非内部操作才发；基类内部会按 operation_kind 自行跳过）
        try:
            await self._send_thinking_end(
                operation_id=operation_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                tool_call_ids=observation.tool_call_ids,
                reasoning_content= observation.reasoning_content,
                cached_tokens=usage.cached_tokens,
                model_id=self.model_name,
                operation_kind=operation_kind,
            )
        except Exception:
            logger.debug("on_thinking_end failed", exc_info=True)

        # 3) complete
        try:
            await self.observer.on_llm_complete(
                provider=self._get_provider_name(),
                model=self.model_name,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                duration_ms=observation.duration_ms(time.time()),
                error=error_code,
                cached_tokens=usage.cached_tokens,
            )
        except Exception:
            native_transport._log_observer_callback_failure(
                "on_llm_complete",
                self.agent_name,
                exc,
            )
    # ------------------------------------------------------------------
    # 重试循环
    # ------------------------------------------------------------------

    async def _call_with_retries(
        self,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject] | None,
        tool_choice: str | chat_model.JsonObject | None,
        kwargs: dict[str, pydantic.JsonValue],
        *,
        operation_id: str,
    ) -> typing.AsyncGenerator[chat_response.ChatResponse, None]:
        params = await self._build_create_params(messages, tools, tool_choice, kwargs)
        extra_body = None
        coordinator = retry_coordinator.ProviderRetryCoordinator(
            provider=self._get_provider_name(),
            model_name=self.model_name,
            agent_name=self.agent_name,
            operation_id=operation_id,
            observer=self.observer,
            sleeper=runtime_interruption.interrupt_sleep,
        )
        # 兼容 projections 为空模块的情况：取不到该类时退化为永不命中的占位类
        _incomplete_error_cls = getattr(native_transport, "_IncompleteOpenAIStreamError", None)
        attempt = 0
        while True:
            # _call_once 是协程：返回 None 表示空响应 / 死流，返回异步生成器表示已取到业务块
            stream = await self._call_once(params, extra_body, attempt_number=attempt + 1)
            if stream is None:
                await coordinator.retry(retry_policy.ProviderRetryCause.EMPTY_RESPONSE)
                attempt += 1
                continue

            committed = True
            try:
                await coordinator.recovered()
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            anext(stream), timeout=self.chunk_idle_timeout
                        )
                    except StopAsyncIteration:
                        return
                    yield chunk
            except asyncio.CancelledError:
                await coordinator.cancelled()
                raise
            except StopAsyncIteration:
                await coordinator.retry(retry_policy.ProviderRetryCause.EMPTY_RESPONSE)
            except Exception as exc:
                if isinstance(exc, (retry_policy.ProviderRetryExhaustedError, retry_policy.ProviderRetryUserFacingError)):
                    raise
                if committed:
                    safe_error = await coordinator.stream_interrupted(exc)
                    raise safe_error from exc
                if isinstance(exc, asyncio.TimeoutError):
                    cause = retry_policy.ProviderRetryCause.FIRST_CHUNK_TIMEOUT
                elif _incomplete_error_cls is not None and isinstance(exc, _incomplete_error_cls):
                    cause = retry_policy.ProviderRetryCause.REQUEST_FAILED
                else:
                    cause = failure_classification.retry_cause(
                        exc,
                        is_stream=self.stream,
                        retyable_hint=_is_retryable_error(exc),
                        policy=self.failure_policy,
                    )
                if cause is None:
                    failure_classification.raise_safe_permanent_error(exc, policy=self.failure_policy)
                await coordinator.retry(cause=cause, error=exc)
            finally:
                aclose = getattr(stream, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        logger.debug("close _call_once generator failed", exc_info=True)
            attempt += 1

    # ------------------------------------------------------------------
    # 单次尝试
    # ------------------------------------------------------------------

    async def _call_once(
        self,
        params: dict[str, object],
        extra_body: dict[str, object] | None,
        *,
        attempt_number: int,
    ) -> "typing.AsyncGenerator[chat_response.ChatResponse, None] | None":
        """单次请求尝试（按入参 ``params`` / ``extra_body`` 发起一次 Responses 调用）。

        返回约定（与 ``_call_with_retries`` 的调用结构对齐）：
        - 流式：返回产出「已投影 ``ChatResponse`` 块」的异步生成器；若预取阶段未取到任何
          业务块（死流 / 空响应），返回 ``None``，交由重试器判定为空响应并重试。
        - 非流式：返回产出单批已投影 ``ChatResponse`` 块的静态生成器。

        底层 ``client`` 与 ``raw_stream`` 的生命周期由生成器自身的 ``finally`` 负责关闭，
        调用方只需消费或丢弃（丢弃时同样会触发 ``finally`` 释放资源）。
        """
        request_params: dict[str, object] = dict(params)
        if extra_body:
            request_params["extra_body"] = extra_body

        client = self._create_responses_client()
        if self.stream:
            try:
                raw_stream = await client.responses.create(**request_params)
            except BaseException:
                await self._safe_close_client(client)
                raise

            try:
                prefetched, continuation = await self._prefetch_business_chunks(
                    raw_stream, timeout=self.chunk_idle_timeout
                )
            except BaseException:
                await self._safe_close_stream(raw_stream)
                await self._safe_close_client(client)
                raise

            if not prefetched:
                # 空响应 / 死流：释放资源并交回 None，交给重试器
                await self._safe_close_stream(raw_stream)
                await self._safe_close_client(client)
                logger.debug(
                    "OpenAIResponsesModel attempt %d: no business chunk prefetched, treating as empty",
                    attempt_number,
                )
                return None

            async def _gen() -> typing.AsyncGenerator[chat_response.ChatResponse, None]:
                try:
                    for chunk in prefetched:
                        yield chunk
                    if continuation is not None:
                        async for chunk in continuation:
                            yield chunk
                finally:
                    await self._safe_close_stream(raw_stream)
                    await self._safe_close_client(client)

            return _gen()

        # 非流式：投影完整 Response 对象
        try:
            response = await client.responses.create(**request_params)
        except BaseException:
            await self._safe_close_client(client)
            raise
        try:
            chunks = list(self._project_response_object(response))
        finally:
            await self._safe_close_client(client)
        return self._static_generator(chunks)


    # ------------------------------------------------------------------
    # 预取业务块（在读流前先取到第一个业务块，空流/死流据此判定为「空响应」）
    # ------------------------------------------------------------------

    async def _prefetch_business_chunks(
        self,
        raw_events: typing.AsyncIterable[object],
        *,
        timeout: int,
    ) -> "tuple[list[chat_response.ChatResponse], typing.AsyncIterator[chat_response.ChatResponse] | None]":
        prefetched: list[chat_response.ChatResponse] = []
        try:
            while True:
                event = await asyncio.wait_for(self._anext(raw_events), timeout)
                for chunk in responses_projection.event_chunks(
                    self._event_to_dict(event)
                ):
                    prefetched.append(chunk)
                if prefetched:
                    break
        except (StopAsyncIteration, asyncio.TimeoutError):
            pass
        except Exception:
            logger.debug("prefetch business chunks failed", exc_info=True)
        if not prefetched:
            return [], None
        return prefetched, self._continue_events(raw_events)

    @staticmethod
    async def _anext(it: typing.AsyncIterable[object]) -> object:
        return await it.__anext__()

    @staticmethod
    def _event_to_dict(event: object) -> dict[str, object]:
        if isinstance(event, dict):
            return event
        dump = getattr(event, "model_dump", None)
        if callable(dump):
            try:
                data = dump()
                if isinstance(data, dict):
                    return data
            except Exception:
                logger.debug("event.model_dump failed", exc_info=True)
        return {}

    async def _continue_events(
        self, raw_events: typing.AsyncIterable[object]
    ) -> typing.AsyncIterator[chat_response.ChatResponse]:
        async for event in raw_events:
            event_dict = self._event_to_dict(event)
            for chunk in responses_projection.event_chunks(event_dict):
                yield chunk

    # ------------------------------------------------------------------
    # 请求参数构建
    # ------------------------------------------------------------------

    async def _build_create_params(
        self,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject] | None,
        tool_choice: object,
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        hooked = await self._apply_chat_params_hook(
            typing.cast("dict[str, object]", kwargs)
        )
        raw_generate_options = hooked.pop("generate_kwargs", None)
        if raw_generate_options is not None and not isinstance(raw_generate_options, dict):
            raise TypeError("generate_kwargs must be a dict")
        generate_options: dict[str, object] = dict(self._init_generate_kwargs)
        generate_options.update(raw_generate_options or {})
        extra_params: dict[str, object] = dict(self.model_kwargs)
        extra_params.update(generate_options)

        instructions, remaining = responses_projection.split_instructions(messages)
        input_items = self._messages_to_responses_input(remaining)

        params: dict[str, object] = {
            "model": self.model_name,
            "input": input_items,
        }
        if instructions:
            params["instructions"] = instructions
        params["stream"] = bool(self.stream)

        converted_tools = responses_projection.convert_tools(tools)
        if converted_tools:
            params["tools"] = converted_tools
            params["tool_choice"] = responses_projection.convert_tool_choice(tool_choice)

        budget = self.output_token_budget
        if budget and budget > 0:
            params["max_output_tokens"] = budget

        for key in ("temperature", "top_p", "top_k", "seed", "reasoning_effort"):
            if key in extra_params and extra_params[key] is not None:
                params[key] = extra_params[key]

        return params

    # ------------------------------------------------------------------
    # 内部消息 → Responses API input 转换
    # ------------------------------------------------------------------

    def _messages_to_responses_input(
        self, messages: object
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        if not isinstance(messages, list):
            return items
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                # system 已由 split_instructions 抽成 instructions，这里不再重复
                continue
            if role == "user":
                items.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": self._extract_text(content)}
                        ],
                    }
                )
            elif role == "assistant":
                content_blocks: list[dict[str, object]] = []
                text = self._extract_text(content)
                if text:
                    content_blocks.append({"type": "output_text", "text": text})
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    tc_items: list[dict[str, object]] = []
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                        name = fn.get("name") if isinstance(fn, dict) else None
                        raw_args = fn.get("arguments") if isinstance(fn, dict) else None
                        if isinstance(raw_args, dict):
                            args = json.dumps(raw_args, ensure_ascii=False)
                        elif isinstance(raw_args, str):
                            args = raw_args
                        else:
                            args = ""
                        call_id = tc.get("id") or f"fc_{uuid.uuid4().hex[:12]}"
                        tc_items.append(
                            {
                                "type": "function_call",
                                "name": name or "",
                                "call_id": call_id,
                                "arguments": args,
                            }
                        )
                    if tc_items:
                        items.append(
                            {
                                "role": "assistant",
                                "content": content_blocks,
                                "tool_calls": tc_items,
                            }
                        )
                        continue
                if content_blocks:
                    items.append({"role": "assistant", "content": content_blocks})
            elif role == "tool":
                call_id = msg.get("tool_call_id")
                output = self._extract_text(content)
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id or "",
                        "output": output,
                    }
                )
            else:
                text = self._extract_text(content)
                if text:
                    items.append(
                        {
                            "role": role,
                            "content": [{"type": "input_text", "text": text}],
                        }
                    )
        return items

    @staticmethod
    def _extract_text(content: object) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    block_type = item.get("type")
                    if block_type in ("text", "input_text", "output_text"):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                    elif "text" in item and isinstance(item.get("text"), str):
                        parts.append(item["text"])
            return "\n".join(part for part in parts if part)
        return ""

    # ------------------------------------------------------------------
    # 非流式：把完整 Response 对象投影成业务块
    # ------------------------------------------------------------------

    def _project_response_object(self, response: object) -> typing.Iterator[chat_response.ChatResponse]:
        data = response
        dump = getattr(response, "model_dump", None)
        if callable(dump):
            try:
                data = dump()
            except Exception:
                logger.debug("response.model_dump failed", exc_info=True)
        if not isinstance(data, dict):
            return
        items = data.get("output")
        blocks: list[message.StreamContentBlock] = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    blocks.extend(responses_projection._responing_item_blocks(item))
        chat_usage = responses_projection.usage(data.get("usage"))
        status = data.get("status")
        finish_reason_fn = getattr(responses_projection, "_finish_reason_from_status", None)
        finish_reason = finish_reason_fn(status) if callable(finish_reason_fn) else None
        yield responses_projection.response_chunk(
            content=blocks, usage=chat_usage, finish_reason=finish_reason
        )

    # ------------------------------------------------------------------
    # 客户端工厂（与 NativeOpenAiChatModel 同形，但走 Responses 端点）
    # ------------------------------------------------------------------

    def _create_responses_client(self, request_headers: dict[str, str] | None = None) -> "openai.AsyncOpenAI":
        proxy_url = self._https_proxy or self._http_proxy
        if request_headers is not None:
            final_headers = dict(request_headers)
        else:
            final_headers = dict(self._default_headers)
            if self._auth_headers:
                final_headers.update(self._auth_headers)
        final_headers["Connection"] = "close"
        effective_api_key = self._auth_api_key or self.api_key or "dummy"
        base_client_kwargs: dict[str, object] = {
            "verify": self.ssl_verify,
            "timeout": httpx.Timeout(600.0, connect=30.0),
            "follow_redirects": True,
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
    def _operation_kind(kwargs: dict[str, object]) -> str:
        value = (
            kwargs.pop("usage_operation_kind", None)
            or kwargs.pop("operation_kind", "normal")
        )
        return value if isinstance(value, str) else "normal"

    def _operation_id(self) -> str:
        import datetime as _dt
        import secrets as _secrets

        timestamp_ms = int(_dt.datetime.now().timestamp() * 1000)
        return f"{self.agent_name}_{timestamp_ms}_{_secrets.token_hex(4)}"

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(float(2 ** max(attempt, 0)), 30.0)

    async def _safe_observer(self, method: str, *args: object, **kwargs: object) -> None:
        fn = getattr(self.observer, method, None)
        if fn is None:
            return
        try:
            await fn(*args, **kwargs)
        except Exception:
            logger.debug("observer %s failed", method, exc_info=True)

    async def _safe_close_stream(self, raw: object) -> None:
        if raw is None:
            return
        aclose = getattr(raw, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:
                logger.debug("close responses stream failed", exc_info=True)

    async def _safe_close_client(self, client: object) -> None:
        if client is None:
            return
        close = getattr(client, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:
                logger.debug("close openai client failed", exc_info=True)

    def _log_retry(self, attempt: int, cause: str, attempt_started_at: float) -> None:
        elapsed = _now() - attempt_started_at
        logger.warning(
            "OpenAIResponsesModel attempt %d failed (%s), retrying (%.1fs elapsed)",
            attempt,
            cause,
            elapsed,
        )

    def _log_empty_retry(self, attempt: int) -> None:
        logger.warning(
            "OpenAIResponsesModel attempt %d returned no business content, retrying",
            attempt,
        )

    @staticmethod
    def _static_generator(
        chunks: list[chat_response.ChatResponse],
    ) -> typing.AsyncGenerator[chat_response.ChatResponse, None]:
        async def _gen() -> typing.AsyncGenerator[chat_response.ChatResponse, None]:
            for chunk in chunks:
                yield chunk

        return _gen()


def _now() -> float:
    return time.monotonic()


def _is_retryable_error(error: BaseException) -> bool:
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError, openai.BadRequestError)):
        return False
    if isinstance(error, openai.RateLimitError):
        return True
    if isinstance(error, openai.APIStatusError):
        return error.status_code >= 500
    return isinstance(error, (openai.APIConnectionError, httpx.TransportError, asyncio.TimeoutError))