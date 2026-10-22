import asyncio
import logging
import secrets
import typing
import uuid

import json
try:
    import anthropic
except ImportError:
    # Anthropic SDK 仅在真正使用 Anthropic provider 时才需要；未安装不应阻塞启动。
    anthropic = None  # type: ignore[assignment]
import time

import domains.agent.domain.aggregates.chat_model as chat_model
from domains.llm.provider.infrastructure.foundation.chat_transport import failure_classification
import domains.llm.provider.infrastructure.families.anthropic.contracts as native_transport
import domains.llm.provider.infrastructure.families.anthropic.request_contracts as request_contracts
import domains.llm.provider.infrastructure.families.anthropic.stream as anthropic_stream
import domains.llm.provider.infrastructure.foundation.chat_transport.retry_coordinator as retry_coordinator
import domains.llm.provider.domain.retry_policy as retry_policy
import datetime as datetime_module
import domains.agent.domain.value_objects.chat_response as chat_contracts
import domains.agent.domain.aggregates.cancellation as cancellation
import domains.agent.domain.value_objects.message as message_module

class AnthropicRequestExecutor:

    def __init__(
        self,
        model: request_contracts.AnthropicRequestModel,
        *,
        interruptible_sleep: request_contracts.InterruptibleSleep,
        request_logger: logging.Logger,
    )->None:
        self._model = model
        self._sleep = interruptible_sleep
        self._logger = request_logger

    async def execute(
        self,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject]|None,
        tool_choice: str|chat_model.JsonObject|None,
        kwargs: dict[str,object],
    )-> request_contracts.ResponseGenerator:
        started_at=time.time()
        operation_kind = self._operation_kind(kwargs)
        operation_id = self._operation_id()
        await self._model._send_thinking_start(
            operation_id,
            messages,
            tools,
            operation_kind=operation_kind,
        )
        call_params = await self._build_call_params(messages, tools,tool_choice, kwargs)
        coordinator= retry_coordinator.ProviderRetryCoordinator(
            provider= self._model._get_provider_name(),
            model_name= self._model.model_name,
            operation_id= operation_id,
            agent_name= self._model.agent_name,
            observer = self._model.observer,
            sleeper = self._sleep,
        )
        try:
            if self._model.stream:
                return await self._execute_stream(
                    call_params,
                    operation_id=operation_id,
                    operation_kind=operation_kind,
                    started_at=started_at,
                    coordinator=coordinator,
                )
            return await self._execute_non_stream(
                call_params,
                operation_id=operation_id,
                operation_kind=operation_kind,
                started_at=started_at,
                coordinator=coordinator,
            )
        except asyncio.CancelledError:
            await coordinator.cancelled()
            raise


    async def _execute_stream(
        self,
        call_params: dict[str,object],
        *,
        operation_id: str,
        operation_kind: str,
        started_at:float,
        coordinator: retry_coordinator.ProviderRetryCoordinator,
    )->request_contracts.ResponseGenerator:
        attempt=0
        while True:
            await self._raise_if_interrupted()
            client: request_contracts.AnthropicClientPort|None = None
            manager: native_transport._AnthropicStreamManagerPort|None = None
            entered=False
            attempt_started_at = time.monotonic()
            try:
                async with asyncio.timeout(self._model.chunk_idle_timeout):
                    resolved_model_id, headers = await self._model._prepare_provider_attempt(attempt+1)
                    params = dict(call_params)
                    params["model"]= resolved_model_id
                    client = self._model._create_anthropic_client(request_headers=headers)
                    manager = client.beta.messages.stream(**params)
                    stream= await manager.__aenter__()
                    entered=True
                    buffered= await self._prefetch_business_events(stream)
                await coordinator.recovered()
                return anthropic_stream.AnthropicStreamResponse(
                    self._model,
                    manager,
                    stream,
                    client,
                    buffered,
                    operation_id=operation_id,
                    operation_kind=operation_kind,
                    started_at=started_at,
                    request_logger=self._logger,
                    rety_lifecycle=coordinator,
                ).responses()
            except asyncio.CancelledError:
                await self._close_stream_attempt(manager, client, entered=entered)
                raise
            except StopAsyncIteration as exe:
                await self._close_stream_attempt(manager, client, entered=entered)
                await coordinator.retry(retry_policy.ProviderRetryCause.EMPTY_RESPONSE, error=exe)
            except asyncio.TimeoutError as exc:
                await self._close_stream_attempt(manager, client, entered=entered)
                self._log_precommit_timeout(attempt_started_at)
                await coordinator.retry(retry_policy.ProviderRetryCause.FIRST_CHUNK_TIMEOUT, error=exc)
            except Exception as exc:
                await self._close_stream_attempt(manager, client, entered=entered)
                cause = failure_classification.rety_cause(
                    exc,
                    is_stream=True,
                    retryable_hint=isinstance(exc, anthropic.APIError),
                    policy=self._model.failure_policy,
                )
                if cause is None:
                    failure_classification.raise_safe_permanent_error(exc, policy=self._model.failure_policy)
                await coordinator.retry(cause,error=exc)
            attempt+=1

    async def _prefetch_business_events(
        self,
        stream: native_transport._AnthropicEventStreamPort,
    )->list[object]:
        buffered: list[object] = []
        while True:
            event = await stream.__anext__()
            buffered.append(event)
            chunk= self._model._anthropic_event_to_chat_response(event)
            if native_transport._chat_response_has_business_output(chunk):
                return buffered


    @staticmethod
    async def _close_stream_attempt(
        manager: native_transport._AnthropicEventStreamManagerPort|None,
        client: request_contracts.AnthropicClientPort|None,
        *,
        entered: bool
    )->None:
        try:
            if manager is not None and entered:
                await native_transport._exit_anthropic_stream_manager(manager, "anthropic")
        finally:
            if client is not None:
                await native_transport._close_provider_client(client,"anthropic")

    @staticmethod
    def _operation_kind(kwargs: dict[str,object]) -> str:
        value= kwargs.pop("usage_operation_kind",None) or kwargs.pop("operation_kind","normal")
        return value if isinstance(value, str) else "normal"

    def _operation_id(self)->str:
        timestamp_ms= int(datetime_module.datetime.now().timestamp()*1000)
        return f"{self._model.agent_name}_{timestamp_ms}_{secrets.token_hex(4)}"

    async def _build_call_params(
        self,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject]|None,
        tool_choice:str|chat_model.JsonObject|None,
        kwargs: dict[str,object],
    )->dict[str,object]:
        hooked_kwargs= await self._model._apply_chat_params_hook(
            typing.cast(chat_model.JsonObject, kwargs),
        )
        if hooked_kwargs is None:
            hooked_kwargs= dict(kwargs)
        raw_generate_options = hooked_kwargs.pop("generate_kwargs",None)
        if raw_generate_options is not None and not isinstance(raw_generate_options, dict):
            raise TypeError("generate_kwargs must be a dict")
        generate_options=dict(self._model._init_generate_kwargs)
        generate_options.update(raw_generate_options or {})
        extra_params=dict(self._model.model_kwargs)
        extra_params.update(generate_options)
        system_text, api_messages = self._model._split_messages(messages)
        self._model._mark_last_message_cacheable(api_messages)
        anthropic_tools= self._prepared_tools(tools)
        max_tokens = extra_params.pop("max_tokens",None) or 16384
        call_params:dict[str,object]={
            "model": self._model.model_name,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        self._add_system(call_params, system_text)
        self._add_tools(call_params, anthropic_tools)
        self._add_tool_choice(call_params, tool_choice)
        self._add_extra_params(call_params, extra_params)
        self._add_default_metadata(call_params)
        return call_params

    def _prepared_tools(
        self,
        tools: list[chat_model.JsonObject]|None,
    )->list[native_transport.ProviderMessage]|None:
        if not tools:
            return None
        return self._model._stabilize_tool_order(self._model._convert_tools(tools))

    def _add_system(
        self,
        call_params: dict[str, object],
        system_text: str | None,
    ) -> None:
        if not system_text:
            return
        system_blocks = self._model._split_system_into_blocks(system_text)
        call_params["system"] = system_blocks or system_text

    @staticmethod
    def _add_tools(
        call_params: dict[str, object],
        tools: list[native_transport.ProviderMessage] | None,
    ) -> None:
        if not tools:
            return
        for tool in tools:
            tool.pop("cache_control",None)
        call_params["tools"] = tools

    @staticmethod
    def _add_tool_choice(
        call_params: dict[str, object],
        tool_choice: str | chat_model.JsonObject | None,
    ) -> None:
        projected:object|None=None
        if tool_choice =="auto":
            projected={"type":"auto"}
        elif tool_choice=="required":
            projected = {"type": "any"}
        elif isinstance(tool_choice, dict):
            projected = tool_choice
        if projected is not None:
             call_params["tool_choice"] = projected

    @staticmethod
    def _add_extra_params(
        call_params: dict[str, object],
        extra_params: dict[str, object] | None,
    ) -> None:
        skipped={"max_tokens","stream","stream_options"}
        call_params.update(
            {
                key:value
                for key,value in extra_params.items()
                if value is not None and key not in skipped
            }
        )

    def _add_default_metadata(
        self,
        call_params: dict[str, object],
    ) -> None:
        if "metadata" in call_params:
            return
        session_id =str(uuid.uuid4())
        call_params["metadata"] = {
            "user_id": json.dumps(
                {
                    "device_id": self._model._cc_device_id(),
                    "account_uuid":"",
                    "session_id": session_id,
                },
                separators=(",", ":"),
            )
        }
        call_params["extra_headers"] = {"X-Claude-Code-Session-Id": session_id}


    async def _execute_non_stream(
        self,
        call_params: dict[str, object],
        *,
        operation_id: str,
        operation_kind: str,
        started_at: float,
        coordinator: retry_coordinator.ProviderRetryCoordinator,
    ) -> request_contracts.ResponseGenerator:
        clients_to_close: list[native_transport._AsyncProviderClientPort] = []
        try:
            attempt=0
            while True:
                 await self._raise_if_interrupted()
                 attempt_started_at = time.monotonic()
                 try:
                       async with asyncio.timeout(self._model.chunk_idle_timeout):
                            response = await self._non_stream_attempt(
                                 call_params,
                                clients_to_close,
                                attempt=attempt,
                                operation_id=operation_id,
                                operation_kind=operation_kind,
                                started_at=started_at,
                                coordinator=coordinator,
                            )
                 except TimeoutError as exc:
                     await native_transport._close_provider_clients(clients_to_close, "anthropic")
                     self._log_precommit_timeout(attempt_started_at)
                     await coordinator.retry(
                         retry_policy.ProviderRetryCause.FIRST_CHUNK_TIMEOUT,
                         error=exc,
                     )
                     response =None
                 if response is not None:
                     await coordinator.recovered()
                     return response
                 attempt += 1
        except Exception as exc:
            await self._report_failure(exc, started_at=started_at)
            raise
        finally:
            await native_transport._close_provider_clients(clients_to_close, "anthropic")
        return self._empty_response()

    async def _non_stream_attempt(
        self,
        call_params: dict[str, object],
        clients_to_close: list[native_transport._AsyncProviderClientPort],
            *,
        attempt: int,
        operation_id: str,
        operation_kind: str,
        started_at: float,
        coordinator: retry_coordinator.ProviderRetryCoordinator,
    ) -> request_contracts.ResponseGenerator|None:
        await self._raise_if_interrupted()
        try:
            resolved_model_id, headers = await self._model._prepare_provider_attempt(attempt+1)
            params = dict(call_params)
            params["model"] = resolved_model_id
            client = self._model._create_anthropic_client(request_headers=headers)
            clients_to_close.append(client)
            raw_message = await client.beta.messages.create(**params)
        except asyncio.CancelledError:
            await native_transport._close_latest_provider_client(clients_to_close, "anthropic")
            raise
        except Exception as exc:
            await native_transport._close_latest_provider_client(clients_to_close, "anthropic")
            cause= failure_classification.retry_cause(
                exc,
                retryable_hint= isinstance(exc, anthropic.APIError),
                policy= self._model.failure_policy,
            )
            if cause is None:
                failure_classification.raise_safe_permanent_error(exc, policy=self._model.failure_policy)
            await coordinator.retry(cause, exc)
            return None
        try:
            response = await self._model._anthropic_message_to_chat_response(raw_message)
        except Exception as exc:
            await native_transport._close_latest_provider_client(clients_to_close, "anthropic")
            await coordinator.retry(retry_policy.ProviderRetryCause.REQUEST_FAILED,error=exc)
            return None
        if not response.content:
            await native_transport._close_latest_provider_client(clients_to_close, "anthropic")
            await coordinator.retry(retry_policy.ProviderRetryCause.EMPTY_RESPONSE)
            return None
        await self._complete_non_streaming_observers(
            response,
            operation_id=operation_id,
            operation_kind=operation_kind,
            started_at=started_at,
        )
        await native_transport._close_latest_provider_client(clients_to_close, "anthropic")
        return self._single_response(response)

    def _log_precommit_timeout(
        self,
        attempt_started_at: float,
    ) -> None:
        self._logger.warning(
            "anthropic request provider=%s,  model=%s, elapsed_ms=%s)",
            self._model._get_provider_name(),
            self._model.model_name,
            int((time.monotonic() - attempt_started_at) * 1000),
        )

    async def _raise_if_interrupted(self) -> None:
        if self._model.observer.is_interrupted():
            await cancellation.propagate_cancelled_error(
                f"LLM call intrrupted for agent {self._model.agent_name}",
            )

    async def _complete_non_streaming_observers(
        self,
        response: chat_contracts.ChatResponse,
        *,
        operation_id: str,
        operation_kind: str,
        started_at: float,
    ) -> None:
        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else 0
        output_tokens = usage.output_tokens if usage is not None else 0
        reasoning_tokens = usage.reasoning_tokens if usage is not None else 0
        cached_tokens = usage.cached_tokens if usage is not None else 0
        try:
            self._model._record_tokens(
                input_tokens,
                output_tokens,
                cached_tokens =cached_tokens,
                operation_kind= operation_kind,
            )
            self._model._detect_cache_break(input_tokens, cached_tokens)
        except Exception as exc:
            logging.error(exc)
        tool_ids=[
            block.id
            for block in (response.content if isinstance(response.content,list) else [])
            if isinstance(block,message_module.ToolUseBlock)
        ]

        try:
            await self._model._send_thinking_end(
                operation_id=operation_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                tool_call_ids= tool_ids,
                cached_tokens= cached_tokens,
                operation_kind= operation_kind,
            )
        except Exception as exc:
            logging.error(exc)

        try:
            await self._model.observer.on_llm_complete(
                provider= self._model._get_provider_name(),
                model=self._model.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                duration_ms=int((time.time() - started_at) * 1000),
                cached_tokens=cached_tokens,
            )
        except Exception as exc:
            native_transport._log_observer_callback_failure(
                "on_llm_complete",
                self._model.model_name,
                exc,
            )

    async def _report_failure(
        self,
        error: Exception,
        *,
        started_at: float,
    ) -> None:
        self._logger.error("error=%s", error)
        duration_ms = int((time.time() - started_at) * 1000)
        try:
            await self._model.observer.on_llm_complete(
                provider=self._model._get_provider_name(),
                model=self._model.model_name,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                duration_ms=duration_ms,
                error= retry_policy.provider_failure_diagnostic_code(error),
            )
        except Exception as exc:
            native_transport._log_observer_callback_failure(
                "on_llm_complete",
                self._model.model_name,
                exc,
            )
    @staticmethod
    async def _single_response(
        response: chat_contracts.ChatResponse,
    ) -> request_contracts.ResponseGenerator:
        yield response

    @staticmethod
    async def _empty_response(
    ) -> request_contracts.ResponseGenerator:
        return
        yield

