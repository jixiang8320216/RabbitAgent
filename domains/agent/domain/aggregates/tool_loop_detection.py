"""工具环（tool loop）检测。

模型偶尔会陷入「用完全相同的参数反复调用同一个工具」的死循环：每一轮都
产生新的工具结果，但推理从不前进。这类循环靠 ``max_iters`` 也能兜住，只是
要白白烧掉十几轮 token。``ToolLoopDetector`` 在一个滑动窗口内按
「工具名 + 参数指纹」记账，重复次数达到阈值就判定成环。

判定成环后由编排层停止本轮工具执行、让模型收敛——入口是
``runtime.service.react_agent_reply`` 的 ``_loop_detected``：它按名字探测
``check`` / ``detect`` / ``__call__``，取返回值真值当结论。本类只做统计，
不碰上下文、不发消息、不抛异常。

误判代价不对称：漏判只是多跑几轮，误判会掐掉正常的多步任务。所以任何
拿不到签名、读不懂入参的情况都走「未成环」。
"""
from __future__ import annotations
import hashlib
import json
import collections
import logging
import typing
import pydantic

import domains.agent.domain.value_objects.tool_loop as tool_loop

logger = logging.getLogger(__name__)

#: 同一签名在窗口内出现多少次算成环。
DEFAULT_THRESHOLD = 3
#: 参与比对的最近调用条数。
DEFAULT_WINDOW_SIZE = 12
#: 成环后最多提示几次（超过后置 ``exhausted``，由调用方决定硬停还是继续）。
DEFAULT_MAX_REMINDERS = 3
#: 日志里展示重复调用时，入参预览截断到多少字符。
DEFAULT_PREVIEW_CHARS = 500
#: ``preview_chars`` 的下限——再小就只剩工具名，失去诊断价值。
MIN_PREVIEW_CHARS = 80

#: 参数指纹取不到的占位符——此时退化为「只比工具名」。
_NO_ARGUMENTS = "-"


class ToolLoopDetector:
    """滑动窗口内的重复工具调用检测。

    :param threshold: 同一「工具名 + 入参指纹」在窗口内累计出现多少次算成环。
        最小为 ``2``（出现一次是正常调用，不是环）。
    :param window_size: 窗口容量，只比对最近这么多条调用。比 ``threshold``
        小时会被抬到 ``threshold``——否则永远凑不满阈值，检测形同虚设。
    :param max_reminders: 成环后最多提示几次。计满后置 :attr:`exhausted`，
        :meth:`check` 仍然返回 ``True``（继续拦工具），调用方可据此决定是
        「再劝一次」还是「直接收尾」。
    :param preview_chars: 日志里展示重复调用时，入参预览截断到多少字符。
        只影响可读性，不参与比对——指纹永远按完整入参算。

    一个实例对应一轮对话：窗口是实例状态，跨轮复用会把上一轮的调用算进来。
    """
    def __init__(
        self,
        threshold: int = DEFAULT_THRESHOLD,
        window_size: int = DEFAULT_WINDOW_SIZE,
        max_reminders: int = DEFAULT_MAX_REMINDERS,
        preview_chars: int = DEFAULT_PREVIEW_CHARS,
    ) -> None:
        self.threshold = max(2, int(threshold or 3))
        self.window_size = max(self.threshold, int(window_size or 12))
        self.max_reminders = max(0, int(max_reminders or 0))
        self.preview_chars = max(
            MIN_PREVIEW_CHARS, int(preview_chars or 500)
        )
        self._window: collections.deque[str] = collections.deque(maxlen=self.window_size)
        self._counts: collections.Counter[str] = collections.Counter()
        self._seen_reminders: set[str]=set()
        self._pending_hits: list[tool_loop.ToolLoopHit] = []
        self._reminder_count= 0

    def record_call(self,*, tool_name: str, arguments:object) -> tool_loop.ToolLoopHit|None:
        if self.max_reminders <= 0:
            return None
        normalized_tool_name =tool_name.strip() or "unknown"
        canonical_args = self._canonical_json(arguments)
        fingerprint = self._fingerprint(normalized_tool_name, canonical_args)
        self._window.append(fingerprint)
        self._counts[fingerprint] += 1
        while len(self._window) >self.window_size:
            old= self._window.popleft()
            self._counts[old] -=1
            if self._counts[old] <=0:
                del self._counts[old]
        count = self._counts[fingerprint]
        if (
            count>=self.threshold
            and fingerprint not in self._seen_reminders
            and self._reminder_count < self.max_reminders
        ):
            hit = tool_loop.ToolLoopHit(
                tool_name=normalized_tool_name,
                args_preview= self._preview(canonical_args),
                count= count,
            )
            self._seen_reminders.add(fingerprint)
            self._pending_hits.append(hit)
            self._reminder_count+=1
            return hit
        return None

    def consume_pending_reminder(self)->str|None:
        if not self._pending_hits:
            return None
        hits=self._pending_hits
        self._pending_hits=[]
        lines=[
            "<system-reminder>",
            "你已经在本次agent run中用相同参数重复调用同一个工具:"
        ]
        for hit in hits:
            lines.append(f"- {hit.tool_name}({hit.args_preview}): 已重置{hit.count} 次")
        lines.extend([
            "",
            "再次调用前，请优先使用对话中已有的工具结果。",
            "如果这次重复调用是有意的，请先说明你期望获得什么新的信息。",
            "</system-reminder>",
        ])
        return "\n".join(lines)

    @classmethod
    def _fingerprint(cls,tool_name:str, canonical_args:str)->str:
        raw=f"{tool_name}\0{canonical_args}".encode("utf-8",errors="replace")
        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def _canonical_json(cls,value:object)->str:
        normalized= cls._normalize(value if value is not None else {})
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    @classmethod
    def _normalize(cls, value:object)-> pydantic.JsonValue:
        if isinstance(value, typing.Mapping):
            return {str(key): cls._normalize(value[key]) for key in sorted(value.keys(),key=lambda item: str(item))}
        if isinstance(value, list):
            return [cls._normalize(item) for item in value]
        if isinstance(value, tuple):
            return [cls._normalize(item) for item in value]
        if isinstance(value, (str,int,float,bool) or value is None):
            return value
        return str(value)

    def _preview(self, canonical_args:str)->str:
        if len(canonical_args)<=self.preview_chars:
            return canonical_args
        return canonical_args[: self.preview_chars-3]+"..."



__all__ = ["ToolLoopDetector"]
