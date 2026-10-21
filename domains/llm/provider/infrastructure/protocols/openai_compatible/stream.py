from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅用于类型标注；运行时不需要，避免拖入 anthropic / 未实现的 projections 桩模块。
    from domains.llm.provider.infrastructure.protocols.openai_compatible import request_contracts
import domains.llm.provider.application.model.capability_resolution as model_capability_resolution
# 下面两行原写作 ``from provider.infrastructure...``（少了 ``llm.`` 前缀），
# 整个模块 import 即抛 ModuleNotFoundError: No module named 'provider'，
# 于是 OpenAI 兼容协议的**流式路径从来没有真正执行过**——所有 chunk 都被丢弃，
# 模型正文永远到不了上层。同时移除无用的 ``import tool`` 与
# realtime 音频缓冲导入（本文件不涉及实时音频）。
import domains.llm.provider.infrastructure.foundation.chat_transport as chat_tranport
from domains.llm.provider.infrastructure.foundation.chat_transport import retry_coordinator

"""OpenAI 兼容协议的**流式**响应消费者：把 SDK 的 chunk 流变成 runtime 的响应生成器。

与 ``families/anthropic/stream.py`` 同一职责：``responses()`` 边收边把 chunk 投影成
``ChatResponse`` yield 给上层，同时把 observer 该收的事件（思考内容、文本增量、工具调用、
流结束、thinking_end、llm_complete）边收边发——UI 的「思考中 / 打字机 / token 统计」全靠
这些旁路事件。

三条约束（与 anthropic 版一致，但第 2 条按 OpenAI 现状调整）：

1. **不反向 import ``request.py``**：``request.py`` import 本模块，反向引用成环；所以所有
   小工具（``_to_chat_response`` 之类）走「能力探测」——模型实现真有
   ``_openai_chunk_to_chat_response`` 就用，没有就退化成 ``None``（跳过该 chunk，不崩）。
2. **类型注解一律字符串** + 对模型的调用走「能力探测」：本项目 OpenAI 侧的 ``projections``
   仍是空模块，硬依赖会 import 即崩；所以关闭 / observer 辅助函数改从真实的
   ``chat_tranport.contracts`` 取，chunk→ChatResponse 的转换器用 getattr 探测。
3. **observer 永远是旁路**：任何打点失败只记 debug，绝不能打断一次已成功的模型调用。

资源归属：本类**独占**流/客户端的关闭权。``request.py`` 的 ``_execute_stream`` 只在「建流
失败 / 预取阶段抛错」分支自己关一次；一旦把生成器返回给上层，收尾（``_finalize`` +
``_close_stream``）就全在 :meth:`responses` 的 ``finally`` 里。
"""

import asyncio
import logging
import time
import typing

import domains.agent.domain.value_objects.chat_response as chat_contracts
import domains.agent.domain.value_objects.message as message_module
import domains.llm.provider.infrastructure.protocols.openai_compatible.projections as native_transport

_LOGGER = logging.getLogger(__name__)
_STREAM_END=object()

class OpenAIStreamResponse:
    """一条 OpenAI 兼容流的**消费者**：产出 ``ChatResponse`` 并顺带发 observer 事件。

    用法（``request.py`` 的 ``_execute_stream`` 里会这样调）::

        return OpenAIStreamResponse(
            model, stream, client, buffered,
            operation_id=..., operation_kind=..., started_at=...,
            request_logger=..., retry_lifecycle=...,
        ).responses()

    ``buffered`` 是预取阶段已经读出来、但还没消费的原始 chunk（用它判断「有产出」）；必须
    **先**把它们吐出去，否则开头几个 chunk 会丢。OpenAI 没有 anthropic 那种独立的 stream
    manager，``stream`` 就是 SDK 的 ``AsyncStream``（或任何 ``__anext__`` 出
    ``ChatCompletionChunk`` 的异步迭代器），``client`` 是要关的 ``AsyncOpenAI``。
    """

    def __init__(
        self,
        model: request_contracts.OpenAIRequestModel,
        stream: native_transport._OpenAIStreamStreamPort,
        client: request_contracts.OpenAIClientPort,
        buffered_raw: list[object],
        *,
        operation_id: str,
        operation_kind: str,
        started_at: float,
        request_logger: logging.Logger,
        retry_lifecycle: retry_coordinator.ProviderRetryCoordinator,
    ) -> None:
        self._model = model
        self._stream = stream
        self._client = client
        self.buffered_raw = list(buffered_raw)
        self._operation_id = operation_id
        self._operation_kind = operation_kind
        self._started_at = started_at
        self._logger = request_logger
        self._retry_lifecycle = retry_lifecycle
        self._last_usage:chat_contracts.ChatUsage|None=None
        self._full_reasoning: list[str]=[]
        self._seen_tool_ids: dict[str, str] = {}
        self._saw_terminal_finish_reason=False
        self._stream_closed=False
        is_minimax= model_capability_resolution.is_minimax_thinking_model(model.model_name)
        self._reasoning_details_state = native_transport._OpenAIReasoningDetailsState()  if is_minimax else None
        self._cumulative_content_state =native_transport._OpenAICumulativeTextState() if is_minimax else None
        self._tool_argument_state= native_transport._OpenAIToolArgumentState()


    async def responses(self) -> request_contracts.ResponseGenerator:
        raw_chunks= iter(self._buffered_raw)
        try:
            while True:
                raw_chunk= next(raw_chunks, _STREAM_END)
                if raw_chunk is _STREAM_END:
                    raw_chunk =await self._next_raw_chunk()
                if raw_chunk is _STREAM_END:
                    break
                chunk= await self._project_chunk(raw_chunk)
                if chunk is not None:
                    yield chunk
                    await self._emit_tool_call_observers(chunk)
                if not self._saw_terminal_finish_reason:
                    raise native_transport._IncompleteOpenAIStreamError(
                        "Openai -compatible stream ened bfoere a termainl final reason"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            safe_error = await self._retry_lifecycle.stream_interrupted(exc)
            raise safe_error from exc
        finally:
            try:
                await self._finalize()
            finally:
                try:
                    await self._close_stream()
                finally:
                    await native_transport._close_provider_client(self._client,"openai-compatible")

    async def _next_raw_chunk(self) -> object:
        if  self._model.observer.is_interrupted():
            await chat_tranport_contracts.propagate_stream_cancellation(
                f"Stream interrupted for {self._model.model_name}"
            )
        try:
            return await asyncio.wait_for(
                self._stream._anext_(),
                timeout= self._model.chunk_idle_timeout,
            )
        except StopAsyncIteration:
            return _STREAM_END
        except asyncio.TimeoutError as exc:
            raise asyncio.TimeoutError(f"Stream idle timeout: no chunk for {self._model.chunk_idle_timeout}") from exc

    # ------------------------------------------------------------------
    # 投影 + 旁路事件
    # ------------------------------------------------------------------

    async def _project_chunk(self, raw_chunk: object) -> chat_contracts.ChatResponse|None:
        chunk = native_transport._openai_chunk_to_chat_response(
            raw_chunk,
            reasoning_details_state= self._reasoning_details_state,
            cumulative_content_state=self._cumulative_content_state,
            tool_argument_state=self._tool_argument_state,
        )
        if chunk is None:
            return None
        if chunk.usage is not None:
            self._last_usage = chunk.usage
        if chunk.finish_reason is not None:
            self._saw_terminal_finish_reason = True
        if not isinstance(chunk.content,list):
            return chunk
        for block in chunk.content:
            if not isinstance(block,message_module.ThinkingBlock) or not block.thinking:
                continue
            self._full_reasoning.append(block.thinking)
            try:
                await self._model.observer.on_thinking_content(
                    self._model.model_name,
                    operation_id=self._operation_id,
                    content=block.thinking,
                )
            except Exception as exc:
                native_transport._log_obsever_callback_failure(
                    "on_thinling_content",
                    self._model.model_name,
                    exc,
                )

    async def _emit_tool_call_observers(self, chunk: chat_contracts.ChatResponse) -> None:
        """把 chunk 里的工具调用增量发给 observer，按 tool_id 去重。"""
        if not isinstance(chunk.content, list):
            return
        for block in chunk.content:
            projection = native_transport.tool_call_observer_projection(block)
            if projection is None:
                continue
            block_id, block_name, input_preview = projection
            if block_id in self._seen_tool_ids:
                continue
            self._seen_tool_ids[block_id] = input_preview
            try:
                await self._model.observer.on_tool_call_streaming(
                    self._model.agent_name,
                    tool_name=block_name,
                    tool_id=block_id,
                    operation_id=self._operation_id,
                    input_preview=input_preview,
                )
            except Exception as exc:  # noqa: BLE001 - 打点是旁路
                native_transport.log_observer_callback_failure(
                    "on_tool_call_streaming", self._model.agent_name, exc
                )

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    async def _finalize(self) -> None:
        input_tokens= self._last_usage.input_tokens if self._last_usage is not None else 0
        output_tokens= self._last_usage.output_tokens if self._last_usage is not None else 0
        reasoning_tokens= self._last_usage.reasoning_tokens if self._last_usage is not None else 0
        cached_tokens= self._last_usage.cached_tokens if self._last_usage is not None else 0
        try:
            self._model._record_tokens(
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cached_tokens,
                operation_kind=self._operation_kind,
            )
        except Exception as exc: self._logger.debug("finalize failed: %s", exc)

        try:
            await self._model._send_thinking_end(
                operation_id=self._operation_kind,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                tool_call_ids=list(self._seen_tool_ids),
                reasoning_content="".join(self._full_reasoning) or None,
                cached_tokens=cached_tokens,
                model_id=self._model.model_name,
                operation_kind=self._operation_kind,
            )
        except Exception as exc: self._logger.debug("finalize failed: %s", exc)

        try:
            await self._model.observer.on_llm_complete(
                provider=self._model._get_provider_name(),
                model=self._model.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                duration_ms=int((time.time()-self._started_at)*1000),
                cached_tokens=cached_tokens,
            )
        except Exception as exc: self._logger.debug("finalize failed: %s", exc)


    async def _close_stream(self) -> None:
         if self._stream_closed:
             return
         self._stream_closed = True
         await native_transport._close_provider_stream(self._stream,"openai-compatible")
