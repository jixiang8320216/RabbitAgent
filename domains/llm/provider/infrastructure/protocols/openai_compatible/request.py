import asyncio
import time
import logging
import typing
import secrets
import datetime as datetime_module
import domains.llm.provider.infrastructure.protocols.openai_compatible.request_contracts as request_contracts
import domains.llm.provider.infrastructure.protocols.openai_compatible.projections as native_transport
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.llm.provider.domain.retry_policy as retry_policy
import domains.llm.provider.infrastructure.foundation.chat_transport.retry_coordinator as retry_coordinator

import json
import openai
import domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification as failure_classification
import domains.llm.provider.infrastructure.foundation.chat_transport.contracts as transport_contracts
import domains.agent.domain.value_objects.chat_response as chat_contracts
import domains.agent.domain.value_objects.message as message_module
import domains.agent.domain.aggregates.cancellation as cancellation

class OpenAIRequestExecutor:

    def __init__(
            self,
            model: request_contracts.AnthropicRequestModel,
            *,
            interruptible_sleep: request_contracts.InterruptibleSleep,
            request_logger: logging.Logger,
    ) -> None:
        self._model = model
        self._sleep = interruptible_sleep
        self._logger = request_logger

    async def execute(
            self,
            messages: list[chat_model.JsonObject],
            tools: list[chat_model.JsonObject] | None,
            tool_choice: str | chat_model.JsonObject | None,
            kwargs: dict[str, object],
    ) -> request_contracts.ResponseGenerator:
        started_at = time.time()
        operation_kind = self._operation_kind(kwargs)
        operation_id = self._operation_id()
        await self._model._send_thinking_start(
            operation_id,
            messages,
            tools,
            operation_kind=operation_kind,
        )
        call_params = await self._build_call_params(messages, tools, tool_choice, kwargs)
        coordinator = retry_coordinator.ProviderRetryCoordinator(
            provider=self._model._get_provider_name(),
            model_name=self._model.model_name,
            operation_id=operation_id,
            agent_name=self._model.agent_name,
            observer=self._model.observer,
            sleeper=self._sleep,
        )
        clients_to_close: list[native_transport._AsyncProviderClientPort]=[]
        try:
            attempt=0
            while True:
                attempt_started_at = time.monotonic()
                try:
                    async with asyncio.timeout(self._model.chunk_idle_timeout):
                        response = await self._execute_attempt(
                            call_params,
                            clients_to_close,
                            attempt=attempt,
                            operation_id =operation_id,
                            operation_kind=operation_kind,
                            started_at=started_at,
                            coordinator=coordinator,
                        )
                except TimeoutError as exc:
                    await native_transport._close_provider_clients(clients_to_close, "openai-compatible")
                    self._log_precommit_timeout(attempt_started_at)
                    await coordinator.retry(
                        retry_policy.ProviderRetryCause.FIRST_CHUNK_TIMEOUT,
                        error=exc,
                    )
                    response=None
                if response is not None:
                    await coordinator.recovered()
                    return response
                attempt+=1
        except asyncio.CancelledError:
            await coordinator.cancelled()
            raise
        except Exception as exc:
            await self._report_failure(exc, started_at=started_at)
            raise
        finally:
            await native_transport._close_provider_clients(clients_to_close, "openai-compatible")

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
        call_params:dict[str,object]={
            "model": self._model.model_name,
            "messages": messages,
            "stream": self._model.stream,
        }
        if self._model.stream:
            call_params["stream_options"] = {"include_usage":True}
        if tools:
            call_params["tools"] = tools
        if tool_choice:
            call_params["tool_choice"] = tool_choice
        call_params.update({key: value for key, value in extra_params.items() if value is not None})
        return call_params

    # ------------------------------------------------------------------
    # 非流式执行（OpenAI 兼容接口）
    # ------------------------------------------------------------------
    # ``execute()`` 直接调用 ``_execute_attempt``；原先该方法缺失，导致每次问答
    # 在真正发请求前就崩。这里用一个**非流式** OpenAI 调用实现：绕过破损的
    # stream 子模块，直接拿 ``ChatCompletion`` 并归一化成 ``ChatResponse``，
    # 让上层 ``react_agent_streaming.collect_model_turn`` 正常抽取文本渲染给 TUI。

    async def _execute_attempt(
        self,
        call_params: dict[str, object],
        clients_to_close: list[native_transport._AsyncProviderClientPort],
        *,
        attempt: int,
        operation_id: str,
        operation_kind: str,
        started_at: float,
        coordinator: retry_coordinator.ProviderRetryCoordinator,
    ) -> request_contracts.ResponseGenerator | None:
        await self._raise_if_interrupted()
        try:
            resolved_model_id, headers = await self._model._prepare_provider_attempt(attempt + 1)
            params = dict(call_params)
            params["model"] = resolved_model_id
            # 强制非流式：避免触发未实现的 OpenAI 流式消费路径。
            params["stream"] = False
            params.pop("stream_options", None)
            client = self._model._create_openapi_client(request_headers=headers)
            clients_to_close.append(client)
            raw = await client.chat.completions.create(**params)
        except asyncio.CancelledError:
            await transport_contracts.close_latest_provider_client(clients_to_close, "openai-compatible")
            raise
        except Exception as exc:
            await transport_contracts.close_latest_provider_client(clients_to_close, "openai-compatible")
            cause = failure_classification.retry_cause(
                exc,
                is_stream=False,
                retryable_hint=isinstance(exc, openai.APIError),
                policy=self._model.failure_policy,
            )
            if cause is None:
                failure_classification.raise_safe_permanent_error(exc, policy=self._model.failure_policy)
            await coordinator.retry(cause, exc)
            return None
        try:
            choice = raw.choices[0]
            msg = choice.message
            text = msg.content or ""
            blocks: list[message_module.StreamContentBlock] = []
            # 思考型模型（DeepSeek-V4 等）会把答案放进 ``reasoning_content``，
            # 而把 ``content`` 留空。原实现只读 ``msg.content``，于是这类模型
            # 的回答被整段丢弃——上游 HTTP 200、``finish_reason='stop'``，
            # 但归一化出来的 ``blocks`` 恒为 ``[TextBlock(text='')]``，
            # 屏上最终一个字都看不到。
            # 这里把 ``reasoning_content`` 作为思考块透传，
            # 并在它同时承载了最终答案时（``content`` 为空）用它兜底正文。
            reasoning = (
                getattr(msg, "reasoning_content", None)
                or getattr(msg, "reasoning", None)
                or ""
            )
            if reasoning:
                blocks.append(
                    message_module.ThinkingBlock(type="thinking", thinking=reasoning)
                )
            if text:
                blocks.append(message_module.TextBlock(type="text", text=text))
            elif reasoning:
                blocks.append(message_module.TextBlock(type="text", text=reasoning))
            for tc in (msg.tool_calls or []):
                blocks.append(
                    message_module.ToolUseBlock(
                        type="tool_use",
                        id=(tc.id or f"tool_{len(blocks)}"),
                        name=(tc.function.name or "unknown"),
                        input=self._safe_json_loads(tc.function.arguments),
                    )
                )
            usage = raw.usage
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            response = chat_contracts.ChatResponse(
                content=blocks if blocks else [message_module.TextBlock(type="text", text="")],
                usage=chat_contracts.ChatUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                finish_reason=choice.finish_reason,
            )
        except Exception as exc:
            await transport_contracts.close_latest_provider_client(clients_to_close, "openai-compatible")
            await coordinator.retry(retry_policy.ProviderRetryCause.REQUEST_FAILED, error=exc)
            return None
        await self._complete_non_streaming_observers(
            response,
            operation_id=operation_id,
            operation_kind=operation_kind,
            started_at=started_at,
        )
        await transport_contracts.close_latest_provider_client(clients_to_close, "openai-compatible")
        return self._single_response(response)

    @staticmethod
    def _safe_json_loads(value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}

    def _log_precommit_timeout(self, attempt_started_at: float) -> None:
        self._logger.warning(
            "openai-compatible request provider=%s, model=%s, elapsed_ms=%s",
            self._model._get_provider_name(),
            self._model.model_name,
            int((time.monotonic() - attempt_started_at) * 1000),
        )

    async def _raise_if_interrupted(self) -> None:
        observer = self._model.observer
        if observer is not None and getattr(observer, "is_interrupted", None) is not None:
            if observer.is_interrupted():
                await cancellation.propagate_cancelled_error(
                    f"LLM call interrupted for agent {self._model.agent_name}"
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
                reasoning_tokens,
                cached_tokens,
                operation_kind=operation_kind,
            )
        except Exception as exc:
            logging.error(exc)
        tool_ids = [
            block.id
            for block in response.content
            if isinstance(block, message_module.ToolUseBlock)
        ]
        try:
            await self._model._send_thinking_end(
                operation_id=operation_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                tool_call_ids=tool_ids,
                cached_tokens=cached_tokens,
                model_id=self._model.model_name,
                operation_kind=operation_kind,
            )
        except Exception as exc:
            logging.error(exc)
        try:
            await self._model.observer.on_llm_complete(
                provider=self._model._get_provider_name(),
                model=self._model.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                duration_ms=int((time.time() - started_at) * 1000),
                cached_tokens=cached_tokens,
            )
        except Exception as exc:
            logging.error(exc)

    async def _report_failure(self, error: Exception, *, started_at: float) -> None:
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
                error=retry_policy.provider_failure_diagnostic_code(error),
            )
        except Exception as exc:
            logging.error(exc)

    @staticmethod
    async def _single_response(response: chat_contracts.ChatResponse) -> request_contracts.ResponseGenerator:
        yield response

