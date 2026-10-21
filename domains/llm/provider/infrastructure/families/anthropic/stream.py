"""Anthropic **流式**响应的消费者：把 SDK 事件流变成 runtime 的响应生成器。

``AnthropicRequestExecutor.execute_on_stream`` 只负责「把流建立起来、并等到第
一个业务事件」（证明这次请求确实有产出），之后就把流交给本类：由它边收边把
事件投影成 ``ChatResponse`` **yield 给上层**，同时把 observer 该收到的东西
（思考内容、文本增量、工具调用、流结束、thinking_end、llm_complete）**边收边
发**出去——UI 上的「思考中 / 打字机效果 / token 统计」全靠这些旁路事件。

三条设计约束：

1. **不能 import ``request.py``**：是 ``request.py`` import 了本模块，反向引用会
   成环。所以 ``_anotify`` / ``_provider_name`` 这些小工具在本模块另存一份
   （与 ``request.py`` 里那份是同一份约定的两个副本，改一处要同步另一处）。
2. **不能 import ``contracts.py`` / ``request_contracts.py``**：前者还是空模块、
   后者在类体里就引用了它的成员。因此**所有类型注解都写成字符串**、对模型的
   调用一律走「能力探测」（``getattr`` + ``callable``）。
3. **observer 永远是旁路**：任何打点失败只记 debug，绝不能反过来打断一次已经
   成功的模型调用。

资源归属：本类**独占**流/管理器/客户端的关闭权。``execute_on_stream`` 只在
「建流失败 / 预取阶段抛错」的分支里自己关一次，一旦把生成器返回给上层，收尾
（``_finalize`` + 关流）就全在 :meth:`responses` 的 ``finally`` 里。
"""

from __future__ import annotations

import asyncio
import logging
import time


import domains.llm.provider.infrastructure.families.anthropic.contracts as native_transport
import domains.llm.provider.infrastructure.families.anthropic.request_contracts as request_contracts
from domains.llm.provider.infrastructure.foundation.chat_transport import retry_coordinator
import domains.agent.domain.value_objects.chat_response as chat_contracts
import domains.agent.domain.value_objects.message as message_module

_LOGGER = logging.getLogger(__name__)


class AnthropicStreamResponse:
    """一条 Anthropic 流的**消费者**：产出 ``ChatResponse`` 并顺带发 observer 事件。

    用法（``execute_on_stream`` 里就这样调）::

        return AnthropicStreamResponse(
            model, manager, stream, client, buffered,
            operation_id=..., operation_kind=..., started_at=...,
            request_logger=..., rety_lifecycle=...,
        ).responses()
    """

    def __init__(
        self,
        model: request_contracts.AnthropicRequestModel,
        manager: native_transport._AnthropicStreamManagerPort,
        stream: native_transport._AnthropicEventStreamPort,
        client: request_contracts.AnthropicClientPort,
        buffered_events: list[object] ,
        *,
        operation_id: str ,
        operation_kind: str,
        started_at: float ,
        request_logger: logging.Logger ,
        rety_lifecycle: retry_coordinator.ProviderRetryCoordinator,
    ) -> None:
        """持有流的所有权，并初始化本轮的**累计状态**。

        ``buffered`` 是 ``execute_on_stream`` 预取阶段已经读出来、但还没消费的
        事件（它靠这批事件判断「有产出」）——必须**先**把它们吐出去，否则开头
        几个 chunk 会丢。

        ``started_at`` 缺省取当前单调时钟：拿不到就当「刚开始」，耗时算 0 比算
        出一个吓人的数字强。``rety_lifecycle`` 只是透传保存（重试由上层协调器
        负责），留着是为了排查时能回溯到是哪一次生命周期。
        """
        self._model = model
        self._manager = manager
        self._stream = stream
        self._client = client
        self._buffered_events= list(buffered_events)
        self._operation_id = operation_id
        self._operation_kind = operation_kind
        self._started_at = started_at
        self._logger = request_logger
        self._retry_lifecycle = rety_lifecycle

        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._reasoning_tokens: int = 0
        self._cached_tokens: int = 0
        self._full_reasoning: list[str] = []
        self._seen_tool_ids: dict[str,str] ={}
        self._finalized: bool = False
        self._saw_terminal_event: bool = False

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def responses(self) -> request_contracts.ResponseGenerator:
        """产出 ``ChatResponse``：先把预取事件吐完，再接着读流直到结束。

        每个事件的处理顺序固定：**中断检查 -> 投影成响应 -> 发 observer -> yield**。
        没有产出的事件（``ping`` / 空增量）被投影成 ``None``，直接跳过——上层
        ``async for`` 只看到「有意义的那部分」。

        收尾放在 ``finally``：无论流正常结束、中途出错、还是消费者提前退出，
        都要补一次打点（否则 UI 会永远停在「思考中」）并关掉流/客户端。收尾里
        的每次 await 都各自包了 ``try``：消费者提前 ``aclose()`` 时这里会撞上
        ``GeneratorExit``，再 await 出 RuntimeError 也不能让它盖掉原本的退出。
        """
        try:
            for event in self._buffered_events:
                async for chunk in self._project_event(event):
                    yield  chunk
            while True:
                await self._raise_if_interrupted()
                try:
                    event= await asyncio.wait_for(
                        self._stream.__anext__(),
                        timeout= self._model.chunk_idle_timeout,
                    )
                except StopAsyncIteration:
                    break
                async for chunk in self._project_event(event):
                    yield chunk
            if not self._saw_terminal_event:
                raise RuntimeError("Anthropic stream ended before message_stop")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.debug("anthropic stream close failed: %s", exc)
            safe_error= await self._retry_lifecycle.stream_interrupted(exc)
            raise safe_error from exc
        finally:
            try:
                await self._finalize()
            except BaseException as exc:  # noqa: BLE001 - 收尾不能改变主链路结果
                self._logger.debug("anthropic stream finalize failed: %s", exc)
                try:
                   await native_transport._exit_anthropic_stream_manager(
                       self._manager,
                       "anthropic",
                   )
                except BaseException as exc:  # noqa: BLE001
                    await native_transport._close_provider_client(self._client, "anthropic")


    async def _project_event(self, event: object) -> request_contracts.ResponseGenerator:
        projected = native_transport._AnthropicEventProjection.model_validate(event)
        if projected.type=="message_stop":
            self._saw_terminal_event = True
        chunk= self._model._anthropic_event_to_chat_response(event)
        if chunk is None:
             return
        await self._observer_chunk(chunk)
        yield chunk
        await self._emit_tool_call_observers(chunk)

    async def _raise_if_interrupted(self) -> None:
        if self._model.observer.is_interrupted():
            await native_transport._propagate_stream_cancellation(
            f"Stream interrupted fro {self._model.agent_name}"
        )

    async def _observer_chunk(self, chunk: chat_contracts.ChatResponse) -> None:
        self._update_usage(chunk.usage)
        if not isinstance(chunk.content, list):
            return
        for block in chunk.content:
            if not isinstance(block, message_module.ThinkingBlock) or not block.thinking:
                continue
            self._full_reasoning.append(str(block.thinking))
            try:
                await self._model.observer.on_thinking_content(
                    self._model.agent_name,
                    operation_id=self._operation_id,
                    content= block.thinking,
                )
            except Exception as exc:
                native_transport._log_observer_callback_failure(
                    "on_thinking_content",
                    self._model.agent_name,
                    exc,
                )

    def _update_usage(self, usage: chat_contracts.ChatUsage) -> None:
        if usage is None:
            return
        if usage.input_tokens:
            self._input_tokens = usage.input_tokens
        if usage.output_tokens:
            self._output_tokens = usage.output_tokens
        if usage.cached_tokens:
            self._cached_tokens = usage.cached_tokens

    async def _emit_tool_call_observers(
        self,
        chunk: chat_contracts.ChatResponse,
    ) -> None:
        if not isinstance(chunk.content, list):
            return
        for block in chunk.content:
            projection = native_transport._tool_call_observer_projection(block)
            if projection is None:
                continue
            block_id, block_name, input_preview = projection
            if block_id in self._seen_tool_ids:
                continue
            self._seen_tool_ids[block_id]=input_preview
            try:
                await self._model.observer.on_tool_call_streaming(
                    self._model.agent_name,
                    tool_name=block_name,
                    tool_id=block_id,
                    operation_id=self._operation_id,
                    input_preview=input_preview,
                )
            except Exception as exc:
                native_transport._log_observer_callback_failure(
                    "on_tool_call_observers",
                    self._model.agent_name,
                    exc,
                )

    async def _finalize(self) -> None:
        """流结束时的**一次性**收尾：文本流结束 -> 缓存探测 -> 记账 -> thinking_end -> complete。

        顺序刻意与非流式的 ``_complete_non_streaming_observers`` 对齐：先内容收尾，
        再 token / 耗时打点——顺序错了 UI 上的耗时和 token 数会对不上。

        :meth:`responses` 的 ``finally`` 保证它一定被调到（正常结束 / 出错 /
        消费者提前退出都是），所以这里做了幂等：重复调用直接返回。
        """
        if self._finalized:
            return
        self._finalized = True
        self._record_tokens()
        self._detect_cache_break()
        await self._send_thinking_end()
        await self._send_completion_observer()

    def _record_tokens(self) -> None:
        try:
            self._model._record_tokens(
                self._input_tokens,
                self._output_tokens,
                0,
                self._cached_tokens,
                operation_kind=self._operation_kind,
            )
        except Exception as exc:  # noqa: BLE001 - 记账是旁路
            self._logger.debug("record tokens failed: %s", exc)

    def _detect_cache_break(self) -> None:
        """探测「前缀缓存突然失效」（缓存读掉的 token 数骤降）。

        这是纯旁路：探测失败、或模型没实现，都不影响响应本身。
        """
        try:
            self._model._detect_cache_break(self._input_tokens, self._cached_tokens)
        except Exception as exc:  # noqa: BLE001
            self._logger.debug("detect cache break failed: %s", exc)

    async def _send_thinking_end(self) -> None:
        """发 ``thinking_end``：本次调用最终的输入/输出/推理 token 与工具调用清单。"""
        try:
            await self._model._send_thinking_end(
                operation_id= self._operation_id,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                reasoning_tokens=0,
                tool_call_ids=list(self._seen_tool_ids),
                reasoning_content="".join(self._full_reasoning) or None,
                cached_tokens=self._cached_tokens,
                model_id=self._model.model_name,
                operation_kind=self._operation_kind,
            )
        except Exception as exc:  # noqa: BLE001 - 打点是旁路
            self._logger.debug("send thinking end failed: %s", exc)

    async def _send_completion_observer(self) -> None:
        try:
            await self._model.observer.on_llm_complete(
                provider= self._model._get_provider_name(),
                model= self._model.model_name,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                reasoning_tokens=0,
                duration_ms=int((time.time()-self._started_at)*1000),
                cached_tokens=self._cached_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - 打点是旁路
            self._logger.debug("send thinking end failed: %s", exc)

