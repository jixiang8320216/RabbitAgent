"""历史消息自动压缩的**执行者**（应用层）。

这是补上的缺口：此前 ``compressionThreshold`` 没消费方、``AgentCompressionState``
是空壳、压缩只作用于系统提示词，于是「token 超阈值 -> 压缩历史」这条链路不存在。

职责分工
--------

- 纯计算（该压哪一段、怎么切才不破坏工具序列）在
  :mod:`domains.context.domain.history_compression_policy`；
- 本模块负责**编排**：按时机调用、拿摘要、写回上下文、维护压缩门状态。

两条压缩路径
------------

1. ``model``：有摘要模型时用模型做摘要（信息密度高）；
2. ``extractive``：**规则式兜底**。摘要模型不可用 / 调用失败时自动降级，
   而不是「那就别压了」——这正是过去缺位的部分（历史上语境一想校验就发现
   「优化了但没优化」）。

关于摘要模型的注入：需要摘要能力时由调用方注入一个
``async (text) -> str`` 的可调用对象（通常是装好 :class:`ContextOptimizer` 的
适配器）。领域层不认识 LLM，应用层也不该硬依赖某个 SDK，这里用回调把两边隔开。
"""

from __future__ import annotations

import logging
import typing

import domains.context.domain.history_compression_policy as policy
import domains.agent.domain.entities.agent_compression_state as compression_state_module

logger = logging.getLogger(__name__)

__all__ = [
    "Summarizer",
    "HistoryCompressionRegistry",
    "HistoryCompressionService",
    "DEFAULT_COMPRESSION_THRESHOLD",
]

#: ``async (window_text) -> summary_text``；返回空串表示「摘要不可用」。
Summarizer = typing.Callable[[str], typing.Awaitable[str]]

#: ``resources/conf/config.json`` 里的默认值；配置读不到时用它兜底。
_DEFAULT_THRESHOLD = 600

#: 对外暴露的默认阈值（宿主和文档都按这个名字引用）。
DEFAULT_COMPRESSION_THRESHOLD = _DEFAULT_THRESHOLD

#: 摘要的字符预算上限；实际预算按窗口 token 规模再往下压。
_MAX_SUMMARY_CHARS = 1200


class HistoryCompressionRegistry:
    """压缩门的状态登记/注销。

    ``AgentScheduler`` 靠 ``get_agent_compression_state(agent_id)`` 返回 ``None``
    判断「可以 spawn 了」，所以压缩结束时**必须注销**，否则子 agent 起不来
    （会一直等到 ``COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS`` 超时）。
    """

    __slots__ = ("_states",)

    def __init__(self) -> None:
        self._states: dict[str, compression_state_module.AgentCompressionState] = {}

    def begin(self, agent_id: str) -> compression_state_module.AgentCompressionState:
        """登记一次压缩开始；同 id 已在压缩时复用既有状态对象。"""
        state = self._states.get(agent_id)
        if state is None:
            state = compression_state_module.AgentCompressionState(agent_id)
            self._states[agent_id] = state
        state.begin()
        return state

    def finish(
            self,
            agent_id: str,
            *,
            strategy: str,
            messages_before: int,
            messages_after: int,
            tokens_before: int,
            tokens_after: int,
    ) -> None:
        """登记完成并注销状态。"""
        state = self._states.pop(agent_id, None)
        if state is None:
            return
        state.finish(
            strategy=strategy,
            messages_before=messages_before,
            messages_after=messages_after,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

    def fail(self, agent_id: str, error: str) -> None:
        """登记失败并注销状态（务必注销，否则 spawn 门挂死）。"""
        state = self._states.pop(agent_id, None)
        if state is not None:
            state.fail(error)

    def get(self, agent_id: str) -> compression_state_module.AgentCompressionState | None:
        """供宿主实现 ``get_agent_compression_state`` 时直接返回。"""
        return self._states.get(agent_id)

    def __len__(self) -> int:
        return len(self._states)


class HistoryCompressionService:
    """``token 超阈值 -> 压缩历史`` 的执行者。"""

    __slots__ = ("_summarizer", "_threshold", "_keep_recent", "_min_compressible", "_registry", "_cooldown", "_since")

    def __init__(
            self,
            *,
            summarizer: Summarizer | None = None,
            threshold: int = _DEFAULT_THRESHOLD,
            keep_recent: int = 4,
            min_compressible: int = 3,
            cooldown_messages: int = 4,
            registry: HistoryCompressionRegistry | None = None,
    ) -> None:
        """
        :param summarizer: 摘要回调；``None`` 时始终走规则式压缩。
        :param threshold: token 阈值（对应配置 ``compressionThreshold``）。
        :param keep_recent: 尾部保留条数。
        :param min_compressible: 至少几条可压才动手。
        :param cooldown_messages: 两次压缩之间至少要新增多少条消息，避免每轮都压。
        :param registry: 压缩门状态登记表；缺省自建。
        """
        self._summarizer = summarizer
        self._threshold = max(1, int(threshold))
        self._keep_recent = max(0, int(keep_recent))
        self._min_compressible = max(1, int(min_compressible))
        self._cooldown = max(0, int(cooldown_messages))
        self._registry = registry or HistoryCompressionRegistry()
        self._since = 0

    # -- 对外主入口 --------------------------------------------------------

    @property
    def registry(self) -> HistoryCompressionRegistry:
        return self._registry

    async def maybe_compress(
            self,
            context: typing.Any,
            *,
            agent_id: str = "",
            metadata: typing.Mapping[str, typing.Any] | None = None,
    ) -> policy.CompressionReport:
        """必要时压缩一次历史；不需要压缩时返回 ``performed=False`` 的报告。

        只要求 ``context`` 提供两件事：``context`` 属性（消息序列）与
        ``replace_window(start, end, message, mark)`` 方法——两者都在
        :class:`domains.agent.domain.value_objects.conversation_context.Context`
        上有实现，所以这里用鸭子类型而不是硬依赖。

        任何异常都被吞掉并转成未压缩报告：压缩是**优化**，不该让一轮对话失败。
        """
        try:
            return await self._maybe_compress(context, agent_id=agent_id, metadata=metadata)
        except Exception as exc:  # noqa: BLE001 - 压缩失败必须降级
            logger.debug("history compression aborted: %s", exc, exc_info=True)
            self._registry.fail(agent_id, str(exc))
            return policy.CompressionReport(
                performed=False,
                strategy="none",
                messages_before=0,
                messages_after=0,
                tokens_before=0,
                tokens_after=0,
                reason=f"error: {type(exc).__name__}: {exc}",
            )

    # -- 内部实现 ----------------------------------------------------------

    async def _maybe_compress(
            self,
            context: typing.Any,
            *,
            agent_id: str,
            metadata: typing.Mapping[str, typing.Any] | None,
    ) -> policy.CompressionReport:
        messages = list(getattr(context, "context", None) or [])
        before_tokens = policy.messages_tokens(messages)

        if self._since < self._cooldown:
            self._since += 1
            return policy.CompressionReport(
                performed=False,
                strategy="none",
                messages_before=len(messages),
                messages_after=len(messages),
                tokens_before=before_tokens,
                tokens_after=before_tokens,
                reason=f"cooldown {self._since}/{self._cooldown}",
            )

        plan = policy.select_compression_window(
            messages,
            threshold=self._threshold,
            keep_recent=self._keep_recent,
            min_compressible=self._min_compressible,
        )
        if plan.end <= plan.start:
            return policy.CompressionReport(
                performed=False,
                strategy="none",
                messages_before=len(messages),
                messages_after=len(messages),
                tokens_before=before_tokens,
                tokens_after=before_tokens,
                reason=plan.reason,
            )

        window = messages[plan.start:plan.end]
        summary_text, strategy = await self._summarize_window(window)

        if not summary_text:
            return policy.CompressionReport(
                performed=False,
                strategy="none",
                messages_before=len(messages),
                messages_after=len(messages),
                tokens_before=before_tokens,
                tokens_after=before_tokens,
                reason="summary unavailable",
            )

        # 「压了反而更胖」的检查：摘要比被替换的那段还长时不要写回。
        # 少了这一步，短对话会被一段啰嗦的摘要撑起来——这是压缩链路最容易
        # 出事的一类退化（做了处理但没有收益，还丢掉了原文）。
        candidate_tokens = (
                before_tokens
                - policy.messages_tokens(window)
                + policy.estimate_tokens(summary_text)
        )
        if candidate_tokens >= before_tokens:
            return policy.CompressionReport(
                performed=False,
                strategy="none",
                messages_before=len(messages),
                messages_after=len(messages),
                tokens_before=before_tokens,
                tokens_after=before_tokens,
                reason=f"no reduction: {before_tokens} -> {candidate_tokens}",
            )

        state = self._registry.begin(agent_id)
        summary_message = self._build_summary_message(summary_text, metadata)
        try:
            replace = getattr(context, "replace_window", None)
            if replace is None or not callable(replace):
                self._registry.fail(agent_id, "context does not support replace_window")
                return policy.CompressionReport(
                    performed=False,
                    strategy="none",
                    messages_before=len(messages),
                    messages_after=len(messages),
                    tokens_before=before_tokens,
                    tokens_after=before_tokens,
                    reason="context.replace_window unavailable",
                )
            replace(plan.start, plan.end, summary_message, policy.COMPRESSION_MARK)
        except Exception as exc:  # noqa: BLE001 - 写回失败同样降级
            self._registry.fail(agent_id, str(exc))
            return policy.CompressionReport(
                performed=False,
                strategy="none",
                messages_before=len(messages),
                messages_after=len(messages),
                tokens_before=before_tokens,
                tokens_after=before_tokens,
                reason=f"replace failed: {exc}",
            )

        after_messages = list(getattr(context, "context", None) or [])
        after_tokens = policy.messages_tokens(after_messages)
        self._registry.finish(
            agent_id,
            strategy=strategy,
            messages_before=len(messages),
            messages_after=len(after_messages),
            tokens_before=before_tokens,
            tokens_after=after_tokens,
        )
        self._since = 0

        logger.info(
            "history compressed: agent=%s strategy=%s %d->%d msgs, %d->%d tokens, run=%s",
            agent_id or "-", strategy, len(messages), len(after_messages),
            before_tokens, after_tokens, state.compression_run_id or "-",
        )
        return policy.CompressionReport(
            performed=True,
            strategy=strategy,
            messages_before=len(messages),
            messages_after=len(after_messages),
            tokens_before=before_tokens,
            tokens_after=after_tokens,
            reason=plan.reason,
        )

    async def _summarize_window(self, window: typing.Sequence[typing.Any]) -> tuple[str, str]:
        """产出摘要文本与所用策略；模型不可用时降级为规则式。

        字符预算按窗口自身的 token 规模给：摘要的目标是**省 token**，
        固定 1200 字符的预算在窗口本来就小的时候只会让结果更胖。
        """
        window_tokens = policy.messages_tokens(window)
        budget = max(120, min(_MAX_SUMMARY_CHARS, window_tokens // 2))

        baseline = policy.extractive_summary(window, max_chars=budget)
        if self._summarizer is None:
            return baseline, "extractive"

        draft = policy.extractive_summary(window, max_chars=budget, per_message_chars=400)
        if not draft:
            return baseline, "extractive"
        try:
            outcome = self._summarizer(_WINDOW_INSTRUCTION.format(content=draft))
            if hasattr(outcome, "__await__"):
                outcome = await outcome
        except Exception as exc:  # noqa: BLE001 - 摘要模型失败 -> 规则式兜底
            logger.debug("model summarizer failed, fallback to extractive: %s", exc)
            return baseline, "extractive"

        text = str(outcome or "").strip()
        if not text:
            return baseline, "extractive"
        return text, "model"

    @staticmethod
    def _build_summary_message(
            summary_text: str,
            metadata: typing.Mapping[str, typing.Any] | None,
    ) -> typing.Any:
        """构造摘要消息。

        延迟导入 ``Msg``：领域层对象不参与本模块的逻辑（只用结构），只有这一步
        需要具体类型；延迟导入同时避免装配期的循环导入。
        """
        import domains.agent.domain.value_objects.message as message_module

        return message_module.Msg(
            name="history-compression",
            content=f"【历史压缩摘要】\n{summary_text}",
            role="system",
            metadata={"kind": "history_summary", **(dict(metadata) if metadata else {})},
        )


_WINDOW_INSTRUCTION = (
    "下面是多轮对话中被判定为「较早历史」的一段消息记录。\n"
    "请压缩成一份**供后续对话参考的摘要**：\n"
    "1. 保留用户对目标的约束、已确认的事实、已经做过的操作与结论；\n"
    "2. 丢弃寒暄、重复表述与可被重新获取的工具原始输出；\n"
    "3. 用中文，条目化，不超过原文的 40% 长度。\n\n"
    "待压缩内容：\n{content}"
)
