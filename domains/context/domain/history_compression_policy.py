"""历史消息压缩的**纯计算策略**（领域层，无第三方依赖）。

为什么单独拆一层
----------------

此前 ``compressionThreshold`` 是一个**没有消费方**的配置项，``AgentCompressionState``
是空壳，压缩只作用于系统提示词（``context_optimizer``）；「token 超阈值 -> 压缩历史」
这条链路没有执行者。补执行者时最容易写成一个「随手调用一下 LLM 总结一把」的方法，
然后卡在三个坑上：

1. **不能切断工具调用序列**。OpenAI 风格的请求里，``assistant`` 带 ``tool_calls``
   后面必须紧跟对应的 ``tool`` 结果；只按 token 数从头砍掉一半历史，会让模型侧
   直接报「tool_call_id 无对应 tool message」；
2. **必须保留系统提示与最近若干轮**，否则模型丢掉当前任务的即时上下文；
3. **要有不依赖模型的兜底**。摘要模型不可用时，压缩也不该变成「什么都不做」——
   退化成规则式摘要（抽取式）仍然能降低 token。

本模块只做**纯计算**（读鸭子类型的消息对象，产出新的消息序列），不碰 IO、不碰模型、
不 import 任何业务模块：

- 保证领域层可在无框架环境下单测（符合「领域层不依赖框架/SDK」的约束）；
- 摘要模型什么时候用、由谁提供，是应用层（:mod:`history_compression` 服务）的事。

消息对象只按结构消费：有 ``role`` / ``content`` / ``metadata`` 属性即可，
因此不反向依赖 ``domains.agent`` 的具体类型。
"""

from __future__ import annotations

import typing

__all__ = [
    "TOOL_BLOCK_TYPES",
    "CompressionPlan",
    "CompressionReport",
    "estimate_tokens",
    "message_text",
    "messages_tokens",
    "has_tool_block",
    "select_compression_window",
    "extractive_summary",
    "apply_compression",
]

#: 工具调用相关内容块的类型；这些消息**不能**被切割（见模块文档第 1 点）。
TOOL_BLOCK_TYPES: frozenset[str] = frozenset({"tool_use", "tool_result"})

#: 一条消息参与摘要时的默认截断长度。
_PER_MESSAGE_CHARS: int = 200

#: 默认压缩标记；写入 ``ContextEntry.marks``，便于事后审计「这段是摘要还是原文」。
COMPRESSION_MARK: str = "compressed"


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF
            or 0xF900 <= code <= 0xFAFF
    )


def estimate_tokens(text: str) -> int:
    """估算 token 数：CJK 按字符计，其余按空白分词。

    为什么不用 tiktoken：它算得准，但会引入第三方 SDK 依赖（领域层不该有），
    而且要联网拉 encoding 文件。这里只需要一个**单调一致**的相对尺度来判断
    「是不是该压了」，近似值足够。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    # 必须先把 CJK 字符挖掉再分词：``split()`` 遇到「全中文且无空格」的串会
    # 返回 **整个串**作为一个 token，于是「你好」被算成 2(CJK) + 1(空白分词) = 3。
    remainder = "".join(" " if _is_cjk(ch) else ch for ch in text)
    others = len([token for token in remainder.split() if token])
    return cjk + others


def message_text(message: typing.Any) -> str:
    """取一条消息的纯文本；内容块只取 ``text`` 类型。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                continue
            if getattr(block, "type", None) == "text":
                value = getattr(block, "text", None)
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return ""


def messages_tokens(messages: typing.Sequence[typing.Any]) -> int:
    """一段消息序列的 token 估算值。"""
    return sum(estimate_tokens(message_text(m)) for m in messages)


def has_tool_block(message: typing.Any) -> bool:
    """该消息是否包含工具调用/工具结果块。"""
    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        return False
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type in TOOL_BLOCK_TYPES:
            return True
    return False


class CompressionPlan(typing.NamedTuple):
    """一次压缩的计划（纯数据，便于单测断言）。

    :param start: 待压缩区间的起始下标（含）。
    :param end: 待压缩区间的结束下标（不含）。
    :param estimated_tokens: 压缩前的消息序列估算 token。
    :param reason: 为什么这样切（或为什么切不了）。
    """

    start: int
    end: int
    estimated_tokens: int
    reason: str


class CompressionReport(typing.NamedTuple):
    """一次压缩的执行结果。

    :param performed: 是否真的压缩了。
    :param strategy: ``"none"`` / ``"model"`` / ``"extractive"``。
    :param messages_before: 压缩前消息条数。
    :param messages_after: 压缩后消息条数。
    :param tokens_before: 压缩前估算 token。
    :param tokens_after: 压缩后估算 token。
    :param reason: 未压缩时说明为什么没压；压缩时说明选择了哪个区间。
    """

    performed: bool
    strategy: str
    messages_before: int
    messages_after: int
    tokens_before: int
    tokens_after: int
    reason: str


def select_compression_window(
        messages: typing.Sequence[typing.Any],
        *,
        threshold: int,
        keep_recent: int = 4,
        min_compressible: int = 3,
) -> CompressionPlan:
    """挑选可安全压缩的区间。

    规则（按重要性排序）：

    1. 估算 token 不超过 ``threshold`` -> 不压；
    2. 头部连续的 ``system`` 消息**永不进入**压缩区间（丢了等于丢当前对话的人设）；
    3. 尾部最近 ``keep_recent`` 条保留；
    4. 切点不能落在工具序列中间：``end`` 处切下去时，``messages[end-1]`` 不能是
       带工具块的消息，``messages[end]`` 也不能是工具结果；遇到这种位置就继续往
       前挪一位，直到找到合法切点；
    5. 可压缩条数少于 ``min_compressible`` -> 不压（压了收益还不如摘要本身占的 token）。

    :param threshold: token 阈值，来自配置的 ``compressionThreshold``。
    :param keep_recent: 保留最近多少条不压。
    :param min_compressible: 至少要有多少条可被压缩才值得动手。
    """
    total = len(messages)
    estimated = messages_tokens(messages)
    if total == 0:
        return CompressionPlan(0, 0, estimated, "empty history")
    if estimated <= max(0, int(threshold)):
        return CompressionPlan(0, 0, estimated, f"tokens {estimated} <= threshold {threshold}")

    # 2) 跳过头部 system 消息
    start = 0
    while start < total and getattr(messages[start], "role", None) == "system":
        start += 1

    # 3) 保留最近 keep_recent 条（含 system 头之后的可用上限）
    upper_bound = max(start, total - max(0, int(keep_recent)))

    # 4) 向前找合法切点：不能切开工具调用 -> 工具结果的成对序列
    end = upper_bound
    while end > start:
        previous = messages[end - 1] if end - 1 >= 0 else None
        current = messages[end] if end < total else None
        if has_tool_block(previous) or (current is not None and has_tool_block(current)):
            end -= 1
            continue
        break

    if end - start < max(1, int(min_compressible)):
        return CompressionPlan(
            0, 0, estimated,
            f"only {end - start} safe messages (< min_compressible {min_compressible})",
        )

    return CompressionPlan(start, end, estimated, f"window [{start}, {end}) of {total}")


def extractive_summary(
        messages: typing.Sequence[typing.Any],
        *,
        max_chars: int = 1200,
        per_message_chars: int = _PER_MESSAGE_CHARS,
) -> str:
    """规则式摘要：**不依赖模型**的兜底压缩。

    做法是「角色前缀 + 截断正文」的行式摘要，工具结果只保留首行 signpost
    （工具名 + 结果首行），因为工具输出的细节通常是下一轮马上要用的，
    留在最近若干轮里；被压缩的历史只需要保留「做过什么」。
    """
    lines: list[str] = []
    total = 0
    for m in messages:
        role = str(getattr(m, "role", "unknown") or "unknown")
        text = " ".join(message_text(m).split())
        if not text:
            continue
        if has_tool_block(m):
            text = f"{text[:per_message_chars]}…(工具输出)"
        elif len(text) > per_message_chars:
            text = text[:per_message_chars] + "…"

        line = f"- {role}: {text}"
        # 行之间的换行符也要算进预算：只累加 ``len(line)`` 会让结果比 max_chars
        # 多出「行数 - 1」个字符（``"\n".join`` 会补这些换行）。
        need = len(line) + (1 if lines else 0)
        if total + need > max_chars:
            remaining = max_chars - total
            if remaining <= 0:
                break
            lines.append(line[:remaining])
            break
        lines.append(line)
        total += need

    return "\n".join(lines)


def apply_compression(
        messages: typing.Sequence[typing.Any],
        plan: CompressionPlan,
        summary_text: str,
        *,
        summary_factory: typing.Callable[[str], typing.Any],
) -> list[typing.Any]:
    """按计划把一段历史替换成一条摘要消息。

    :param summary_factory: 由调用方提供「文本 -> 消息对象」的构造器，
        本模块因此不需要知道 ``Msg`` 的具体类型（也不引入它）。
    :return: 新的消息序列；``plan`` 不合法或摘要为空时原样返回。
    """
    if not summary_text or not summary_text.strip():
        return list(messages)
    if plan.end <= plan.start or plan.start < 0 or plan.end > len(messages):
        return list(messages)

    summary_message = summary_factory(summary_text)
    return [*messages[:plan.start], summary_message, *messages[plan.end:]]
