"""ReAct 回复编排。

一次用户消息进来后，:class:`runtime.service.react_agent.ReActAgent` 需要完成
「推理 -> 工具调用 -> 观察回流 -> 终止」这一轮 iterated 对话。这段逻辑如果直接
写在 ``ReActAgent`` 里，那个类会迅速膨胀到难以维护（它还要兼顾 hook 注册、
 Registry 状态、持久化、执行存储），因此这里把它拆成独立的编排对象：

    ReActAgent.reply -> run_react_reply -> ReactReplyOrchestrator.run

职责边界
--------

- **本对象只负责一次回复的执行流**：迭代上限、hook 时机、消息如何流进上下文、
  模型产物如何归一化成 :class:`runtime.model.message.Msg`、工具结果如何回灌。
- **不负责** ReActAgent 自身的生命周期（状态注册、持久化、默认端口 Matters），
  那些仍归宿主；本对象只在最后把 ``_last_response_text`` /
  ``_last_run_iterations`` 写回宿主，供 observer 与调度层读取。

为什么到处都是签名探测
----------------------

本层的依赖全部是通过构造函数注入的鸭子类型，且其中几个模块目前还是空的：

- ``runtime.service.chart_model.ChartModel``（模型调用方）
- ``runtime.service.message_formatter.MessageFormatter``（请求格式化）
- ``runtime_ports.ToolkitProtocol``（工具执行）
- ``runtime.model.conversation_context.Context``（上下文）

也就是说它们**没有任何稳定形状**。因此这里统一用
:meth:`ReactReplyOrchestrator._probe` 做「先按签名绑定，再调用」的探测：
``inspect.signature.bind`` 成功才真的调用，这样可以避免把模型/工具内部抛出的
``TypeError`` 误判成「签名不匹配」而静默跳过。

降级原则：读取类能力（历史、工具清单、格式化）失败一律退化为空/原样；
只有**模型调用**和**工具执行**会真的抛错——前者没有模型，后者工具执行不了，
都不可假装成功。
"""

from __future__ import annotations

import collections.abc
import collections.abc as abc
import inspect
import logging
import datetime as datetime_module

import domains.agent.domain.entities.agent_transcript as agent_transcript

import domains.agent.domain.value_objects.tool_response as tool_response_model
import domains.agent.domain.entities.agent_context as agent_context_module
import domains.agent.domain.value_objects.message as message
import domains.agent.domain.aggregates.react_agent_collaboration as react_contracts
import domains.agent.domain.aggregates.react_agent_tool_execution as tool_execution
import domains.agent.domain.aggregates.tool_wait_budget as tool_wait_budget
import domains.agent.domain.aggregates.react_agent_streaming as react_streaming
import domains.agent.domain.aggregates.tool_loop_detection as tool_loop_detection
import domains.agent.domain.value_objects.tool_call as tool_call_model
__all__ = ["run_react_reply", "ReactReplyOrchestrator"]

_LOG_PREFIX = "[REACT-REPLY] "

#: 宿主未指定 ``max_iters`` 时的安全上限。
#:
#: :meth:`run` 的循环条件是 ``max_iters is None or iteration < max_iters``——
#: ``max_iters`` 为 ``None`` 时是**无界**的。模型一旦执着地反复调用工具，这里会
#: 一直烧下去，因此 :meth:`_run_iteration` 在 ``max_iters`` 缺失时自行封顶。
_DEFAULT_MAX_ITERS = 10

#: 工具结果写回上下文时使用的角色。``Msg`` 只允许 user/assistant/system，
#: 因此 OpenAI 的 ``tool`` 角色在这里只能落成 ``user``。
_TOOL_RESULT_ROLE = "user"

logger = logging.getLogger(__name__)

async def run_react_reply(
    host: react_contracts.ReactAgentRuntime,
    msg: message.Msg,
    *,
    app_resolver: collections.abc.Callable[[], object | None],
    session_id_resolver: collections.abc.Callable[[], str | None],
    event_logger: logging.Logger,
)->message.Msg:
    return await ReactReplyOrchestrator(
        host,
        app_resolver=app_resolver,
        session_id_resolver=session_id_resolver,
        event_logger=event_logger,
    ).run(msg)




class ReactReplyOrchestrator:
    """一次 ReAct 回复的执行编排器。

    :param host: 正在回复的 :class:`ReActAgent` 宿主；本对象只读它的依赖
        （``model`` / ``formatter`` / ``toolkit`` / ``context`` / ``_sys_prompt``）
        并回写少量执行结果。
    :param app_resolver: 取回应用实例的回调；每轮开始时的 hook payload 会带上它，
        供 hook 访问会话状态。允许返回 ``None``。
    :param session_id_resolver: 取回会话标识的回调；用于中断检查与日志。
    :param event_logger: 事件日志器。

    生命周期：一次 :meth:`run` 对应一次用户消息，**不可复用**——内部持有本轮的
    局部历史与迭代计数。
    """

    def __init__(
        self,
        host: react_contracts.ReactAgentRuntime,
        *,
        app_resolver: collections.abc.Callable[[], object | None],
        session_id_resolver: collections.abc.Callable[[], str | None],
        event_logger: logging.Logger,
    ) -> None:
        self._host = host
        self.event_logger = event_logger
        #: 工具调用退回 :meth:`_invoke_tool` 的签名探测，不让构造阶段就炸。
        self._tool_executor = tool_execution.ToolBatchExecutor(
            host, app_resolver=app_resolver, session_id_resolver=session_id_resolver
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def run(self, msg: message.Msg) -> message.Msg:
        """执行一轮 ReAct 回复。

        :param msg: 用户消息，入场时写进上下文。
        :return: 助手消息；正常情况下 content 是文本（或带文本块的内容列表）。
        """
        with tool_wait_budget.tool_wait_budget_scope():
            await self._host.context.add(msg)
            self._host._last_run_iterations=0
            tool_loop_detector = self._host._create_tool_loop_detector()
            max_iters= self._host.max_iters
            iteration=0
            while max_iters  is None or iteration < max_iters:
                response = await self._run_iteration(iteration,tool_loop_detector)
                if response is not None:
                    return response
                iteration += 1
            return await self._host._summarize()

    async def _notify_text_stream_end(self)->None:
        try:
            await self._host.model.observer.on_text_stream_end(self._host.name)
        except Exception as exc:
            logger.exception(exc)

    async def _run_iteration(
        self,
        iteration: int,
        tool_loop_detector: tool_loop_detection.ToolLoopDetector|None,
    ) -> message.Msg | None:
        """跑一轮「推理 -> 工具 -> 观察」。

        :param iteration: 0 起的迭代序号，由 :meth:`run` 递增。
        :param tool_loop_detector: 宿主创建的工具环检测器（可能为 ``None``）。
        :return: **本轮已终局**时返回助手消息；还有工具要跑则返回 ``None``，
            让 :meth:`run` 继续下一轮。

        终局条件有四种：本轮已被中断、模型没再要工具、工具环被检出、
        ``max_iters`` 缺失时的安全封顶。
        """
        self._host._last_run_iterations = iteration+1
        self._mark_iteration_started(iteration)
        await self._host._fire_hooks(
            "pre_reasoning",
            {
                "iteration": iteration,
                "agent_name": self._host.name,
            },
        )
        turn = await self._collect_turn(iteration)
        response = message.Msg(
            self._host.name,
            turn.content if turn.content else "",
            "assistant",
        )

        if self._host.tool_call_mode =="text_parse":
            #文本标记及学习模式（simpleAgent/ToolAwareSimpleAgent）
            text=response.text or ""
            tool_calls = self._host._parse_text_tool_calls(text)
            if not tool_calls:
                # 无工具调用-》终局
                return await self._complete_without_tools(response, turn, iteration)
            # 有工具调用-》执行
            tool_results = await self._host._execute_text_tool_calls(text)
            for result_msg in tool_results:
                await self._host_context.add(result_msg)

            return None #继续下一轮
        else:
            #原生 tool_use块模式
            #
            # 此前写的是 ``response.tool_use_blocks``——项目里 ``Msg`` 没有这个
            # 属性（那是 Anthropic 原生块的命名），取属性直接抛
            # ``AttributeError: 'Msg' object has no attribute 'tool_use_blocks'``。
            # 本项目统一的取法是 ``get_content_blocks("tool_use")``。
            tool_use_blocks = response.get_content_blocks("tool_use")
            if not tool_use_blocks:
                return await self._complete_without_tools(response, turn, iteration)

        self._persist_assistant_response(response, iteration)
        response = await self._apply_post_reasoning_hook(response)
        tool_uses = response.get_content_blocks("tool_use")
        if not tool_uses:
            return await self._complete_without_tools(response, turn, iteration)
        malformed_results, dispatchabled_tools = _partition_malformed_tools(
            self._host.name,
            list(tool_uses),
            turn.tool_call_errors,
        )

        await self._prepare_tool_calls(dispatchabled_tools, tool_loop_detector)
        await self._host.context.add(response)
        self._mark_tool_started(dispatchabled_tools, iteration)
        executed_results = await self._tool_executor.execute(dispatchabled_tools)
        tool_results = [*malformed_results, *executed_results]
        await self._host._fire_hooks(
            "post_acting",
            {"tool_results": tool_results, "agent_name" : self._host.name},
        )
        await self._host._dispatch_post_tool_batch(tool_results)
        await self._store_tool_results(tool_results,tool_loop_detector,iteration)
        await self._host._fire_hooks(
            "post_observe",
            {"tool_results":tool_results,"agent_name": self._host.name},
        )
        return None

    async def _collect_turn(self, iteration: int) -> react_streaming.ModelTurn :
        system_message= message.Msg("system", self._host._sys_prompt, "system")
        memory_messages= await self._host.context.get_messages()
        formatted = await self._host.formatter.format(msgs=[system_message, *memory_messages])
        tool_schema = self._host.toolkit.get_json_schemas() if self._host.toolkit else []
        model_stream = await self._host._call_model_with_error_recovery(
            formatted,
            tool_schema,
            iteration = iteration,
        )
        turn= await react_streaming.collect_model_turn(
            model_stream,
            agent_name = self._host.name,
            emit_text_delta = self._host._emit_text_delta,
            emit_tool_delta = self._host._emit_tool_call_streaming,
            notify_text_stream_end = self._notify_text_stream_end,
            event_logger = self.event_logger,
        )
        return turn

    def _mark_iteration_started(self, iteration: int) -> None:
        try:
            if self._host.agent_context is not None:
                self._host.agent_context.iteration = iteration
                self._host.agent_context.status = agent_context_module.AgentStatus.RUNNING
            self._host._append_transcript_event(
                # AgentTranscriptEvent 是 frozen pydantic 模型，并没有
                # iteration_started(...) 工厂方法（只有枚举 ITERATION_STARTED）。
                # 原调用会抛 "AttributeError: iteration_started"，
                # 并连带把同一 try 块里已经成功的 iteration/status 赋值一起废掉。
                agent_transcript.AgentTranscriptEvent(
                    event_type=agent_transcript.AgentTranscriptEventType.ITERATION_STARTED,
                    iteration=iteration,
                    agent_name=self._host.name,
                    timestamp=datetime_module.datetime.now(),
                )
            )
        except Exception as exc:
            logger.exception("iteration-start bookkeeping failed: %s", exc)

    def _persist_assistant_response(self, response:message.Msg, iteration: int) -> None:
        try:
            self._host._append_transcript_message(response, iteration=iteration)
            self._host._persist_state()
        except Exception as exc:
            logger.error(exc)

    def _persist_truncation(
        self,
        turn: react_streaming.ModelTurn,
        iteration: int,
    ) -> None:

        try:
            self._host._append_transcript_event(
                # 同上：没有 response_truncated(...) 工厂方法，直接构造模型。
                # timestamp 用 naive 本地时间，与 AgentContext.last_heartbeat 一致；
                # 原代码传 aware(UTC) 而下游做 naive/aware 相减会抛
                # "can't subtract offset-naive and offset-aware datetimes"。
                agent_transcript.AgentTranscriptEvent(
                    event_type=agent_transcript.AgentTranscriptEventType.RESPONSE_TRUNCATED,
                    iteration=iteration,
                    finish_reason=turn.finish_reason,
                    stop_details=turn.stop_details,
                    text_length=len(turn.full_text),
                    tool_count=0,
                    timestamp=datetime_module.datetime.now(),
                )
            )
            if self._host.agent_context is not None:
                self._host.agent_context.status= agent_context_module.AgentStatus.COMPLETED
        except Exception as exc:
            logger.exception("truncation bookkeeping failed: %s", exc)

    def _persist_completion(self, iteration: int) -> None:
        try:
            if self._host.agent_context is not None:
                self._host.agent_context.status = agent_context_module.AgentStatus.COMPLETED
            self._host._persist_state()
        except Exception as exc:
            logger.error(exc)

    async def _apply_post_reasoning_hook(self, response:message.Msg)-> message.Msg:
        hook_context= await self._host._fire_hooks(
            "post_reasoning",
            {"response": response, "agent_name": self._host.name},
        )
        hook_response= hook_context.get("response", response)
        if not isinstance(hook_response, message.Msg):
            raise TypeError("post_reasoning hook response is not Msg")
        return hook_response

    async def _prepare_tool_calls(
        self,
        tool_uses: list[message.ToolUseBlock],
        detector: tool_loop_detection.ToolLoopDetector|None,
    ) -> None:
        for tool_call in tool_uses:
            hook_context=  await self._host._fire_hooks(
            "pre_acting",
            {"tool_call": tool_call, "agent_name": self._host.name},
            )
            hooked_tool_call = hook_context.get("tool_call", tool_call)
            if not isinstance(hooked_tool_call, message.ToolUseBlock):
                raise TypeError("pre_acting: tool_call should be a ToolUseBlock")
            tool_call.update(hooked_tool_call)
        if detector is not None:
            for tool_call in tool_uses:
                detector.record_call(
                    tool_name=tool_call.name or "unknown",
                    arguments= tool_call.input,
                )

    def _mark_tool_started(
        self,
        tool_uses: list[message.ToolUseBlock],
        iteration: int,
    ) -> None:
        try:
            tool_names=[tool_use.name for tool_use in tool_uses]
            if self._host.agent_context is not None:
                self._host.agent_context.status = agent_context_module.AgentStatus.WAITING_TOOL
                self._host.agent_context.current_tool=",".join(tool_names)
                self._host._persist_state()
            nonempty_names = tuple(name for name in tool_names if name.strip())
            if nonempty_names:
                self._host._append_transcript_event(
                    agent_transcript.AgentTranscriptEvent.tool_started(
                        tool_name=nonempty_names,
                        timestamp= datetime_module.datetime.now(tz=datetime_module.UTC),
                    )
                )
        except Exception as exc:
            logger.error(exc)

    async def _store_tool_results(
        self,
        tool_results: list[react_contracts.ToolResult],
        detector: tool_loop_detection.ToolLoopDetector|None,
        iteration: int,
    ) -> None:
        result_blocks=[_tool_result_block(tool_use, response) for tool_use,response in tool_results]
        result_message=message.Msg("system", result_blocks,"system")
        await self._host.context.add(result_message)
        reminder_message= await self._store_loop_reminder(detector, iteration)
        self._persist_tool_completion(result_message, reminder_message, len(result_blocks),iteration)

    async def _complete_without_tools(
        self,
        response: message.Msg,
        turn: react_streaming.ModelTurn,
        iteration: int,
    ) -> message.Msg:
        """模型没再要工具 —— 本轮就是终局回复，收尾后返回。

        :param response: ``_run_iteration`` 刚归一化出来的助手消息。
        :param turn: 本轮原始产物，用它的 ``full_text`` 回写「最后回复文本」。
        :param iteration: 0 起的迭代序号，随 hook payload 带给插件。
        :return: 终局助手消息（``pre_print`` 钩子改写后的那一条）。

        回写 ``_last_response_text`` 是硬要求：宿主 ``_summarize`` 与
        ``agent_call_lifecycle_hooks`` 都靠 ``take_last_response_text()`` 取最终
        文本，不写等于把整轮回复丢掉。

        ``pre_print`` / ``post_print`` 两个钩子挂在这里：前者在文本定稿之前
        （可改写），后者在回写完成之后（纯通知）。
        """
        # ``ModelTurn.truncated`` 是**方法**，不是 property。原代码写成
        # ``if turn.truncated:``拿到的是 bound method 对象，永远为真——于是每一次
        # 正常回复都被当成「上游截断」，正文被替换成
        # "[Upstream truncated: ...]" 提示，屏上永远看不到模型答案。
        # 必须调用它：``turn.truncated()``。
        if turn.truncated():
            response = _truncated_response(self._host.name,turn)
            await self._host.context.add(response)
            self._persist_truncation(turn,iteration)
            notice= _truncation_notice(turn)
            self._host._last_response_text = notice+(
                "\n\n"+turn.full_text if turn.full_text else ""
            )
            return response
        self._host._last_response_text = turn.full_text or None
        await self._host.context.add(response)
        self._persist_completion(iteration)
        return response

    async def _store_loop_reminder(
         self,
         detector: tool_loop_detection.ToolLoopDetector|None,
         iteration:int,
    )->message.Msg|None:
         if detector is None:
            return None
         reminder_text= detector.consume_pending_reminder()
         if not reminder_text:
             return None
         reminder = message.Msg(
        "system",
              reminder_text,
         "system",
              metadata={"source": "runtime",
                  "kind": "tool_loop_reminder",
                  "iteration": iteration,},
         )
         await self._host.context.add(reminder)
         return reminder

    def _persist_tool_completion(
         self,
         result_message: message.Msg,
         reminder_message: message.Msg|None,
         tool_count:int,
         iteration:int,
         )->None:
         try:
             self._host._append_transcript_message(result_message, iteration=iteration)
             if reminder_message is not None:
                 self._host._append_transcript_message(reminder_message, iteration=iteration)
             self._host._append_transcript_event(
                  agent_transcript.AgentTranscriptEvent.tool_completed(
                      tool_count=tool_count,
                      timestamp=datetime_module.datetime.now(tz=datetime_module.UTC),
                   )
             )
             if self._host.agent_context is not None:
                  self._host.agent_context.status= agent_context_module.AgentStatus.RUNNING
                  self._host.agent_context.current_tool=None
             self._host._persist_state()
         except Exception as e:
              logger.error(e)

def _partition_malformed_tools(
    agent_name: str,
    tool_uses: list[message.ToolUseBlock],
    errors: dict[str,tool_call_model.ToolInputError],
)-> tuple[list[react_contracts.ToolResult], list[message.ToolUseBlock]]:
    malformed_results: list[message.ToolUseBlock] = []
    dispatchable: list[message.ToolUseBlock] = []
    for tool_use in tool_uses:
        input_error= errors.get(tool_use.id)
        if input_error is None:
            dispatchable.append(tool_use)
            continue
        diagnostic=(
            f"MalformedToolCallError: tool '{tool_use.name} was not dispatched -'"
            f"{input_error.reason}. Raw arguments seen: {input_error.raw_input!r}."
            "Re-issue thr tool call with thr full JSON arguments."
        )

        malformed_results.append(
            (
                tool_use,
                tool_response_model.ToolResponse(
                    content=[message.TextBlock(type="text", text=diagnostic)],
                    metadata={"status": "malformed_tool_call"},
                )
            )
        )
    return malformed_results, dispatchable

def _tool_result_block(
    tool_use: message.ToolUseBlock,
    response: tool_response_model.ToolResponse,
)->message.ToolResultBlock:
    output= response.content or [message.TextBlock(type="text", text="")]
    return message.ToolResultBlock(
        type="tool_result",
        id=tool_use.id,
        name=tool_use.name,
        output=output,
    )

def _truncation_notice(turn: react_streaming.ModelTurn) -> str:
    details_suffix=f"stop_details={turn.stop_details!r}" if turn.stop_details else ""
    return (
        f"[Upstream truncated: finish_reason={turn.finish_reason!r}{details_suffix}] "
        "The model was cut off before emitting a tool_use. Raise max_tokens or reduce"
        "input size and retry"
    )

def _truncated_response(agent_name:str, turn:react_streaming.ModelTurn)->message.Msg:
    content: list[message.ContentBlock] = [message.TextBlock(type="text", text=_truncation_notice(turn))]
    if turn.full_thinking:
        content.append(message.ThinkingBlock(type="thinking", thinking=turn.full_thinking))
    if turn.full_text:
        content.append(message.TextBlock(type="text", text=turn.full_text))
    return message.Msg(agent_name, content,"assistant")












