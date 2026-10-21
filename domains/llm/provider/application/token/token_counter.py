"""Token 计数与按 agent 维度的用量累计。

``TokenCounter`` 有两个职责：

1. **估算单条消息的 token 数** —— 委托给 :mod:`openai_token_counting` 的实现
   （优先 tiktoken 精确编码，缺失时退化为启发式估算）
2. **累计实际 API 返回的用量** —— 按 ``agent_name`` 与 ``operation_kind``
   两个维度分别累计 prompt / completion / reasoning / cached token

**为什么要有累计**：LLM 调用的真实用量只有 API 响应里才知道（本地估算只是
预估）。``provider_chat_model`` 在收到响应后会回调本对象把真实用量记下来，
供预算控制、上下文压缩与可观测性使用。

**历史缺口**：本模块此前只实现了 ``__init__``，所有记录方法都缺失。
``provider_chat_model`` 因此用 ``getattr`` 做能力探测，找不到就静默跳过 ——
token 统计一直处于「不报错但也不工作」的状态。这里按调用契约补齐。
"""

from __future__ import annotations

import typing

import domains.llm.provider.application.token.openai_token_counting as openai_token_counting


@typing.runtime_checkable
class MemoryTokenSink(typing.Protocol):
    """可接收「最近一次 API 用量」的下游（如记忆系统）。

    记忆压缩需要知道「最近这轮用了多少 token」来判断是否该触发压缩，
    因此 TokenCounter 会把最近一次的用量推给已登记的 sink。
    """

    def set_last_api_token(self, *, input_token: int, output_tokens: int) -> None:
        """接收最近一次 API 调用的 token 用量。"""


class TokenCounter:
    """Token 计数器：本地估算 + 实际用量累计。"""

    def __init__(self, model_name: str = "gpt-4"):
        self.model_name = model_name
        self._counter = openai_token_counting.OpenAILocalTokenCounter(model_name=model_name)

        # Per-agent token tracking (cumulative)
        # 结构：agent_name -> {计数字段名 -> 累计值}
        self._agent_tokens: typing.Dict[str, typing.Dict[str, str]] = {}
        self._agent_last_tokens: typing.Dict[str, typing.Dict[str, str]] = {}

        # 结构：agent_name -> operation_kind -> {计数字段名 -> 累计值}
        self._agent_tokens_by_kind: typing.Dict[str, typing.Dict[str, typing.Dict[str, str]]] = {}
        self._agent_last_tokens_by_kind: typing.Dict[str, typing.Dict[str, typing.Dict[str, str]]] = {}

        # 已登记的记忆 sink（按 agent 维度）
        self._agent_memoried: dict[str, MemoryTokenSink] = {}

        #: 最近一次 set_api_tokens 的 agent 名，供简化入口使用
        self._last_agent_name: str = ""
        #: 最近一次记录的用量
        self._last_api_tokens: typing.Dict[str, str] = {}

    # ------------------------------------------------------------------
    # 计数
    # ------------------------------------------------------------------

    async def count(
        self,
        message: list[typing.Any],
        **kwargs: object,
    ) -> int:
        """估算这批消息会消耗多少 token。"""
        return await self._counter.count(message, **kwargs)

    # ------------------------------------------------------------------
    # 实际用量累计
    # ------------------------------------------------------------------

    def set_api_tokens(
        self,
        *,
        agent_name: str,
        prompt_tokens: int,
        completed_tokens: int,
        reasoning_tokens: int = 0,
        cached_tokens: int = 0,
        operation_kind: str = "normal",
    ) -> None:
        """记录一次 API 调用的真实用量。

        同时按 agent 维度与 (agent, operation_kind) 维度累计，
        并把最近一次的用量推给该 agent 登记的记忆 sink。
        """
        if not agent_name:
            return

        values = {
            "prompt_tokens": str(int(prompt_tokens)),
            "completed_tokens": str(int(completed_tokens)),
            "reasoning_tokens": str(int(reasoning_tokens)),
            "cached_tokens": str(int(cached_tokens)),
            "total_tokens": str(int(prompt_tokens) + int(completed_tokens)),
        }

        # agent 维度累计
        agent_bucket = self._agent_tokens.setdefault(agent_name, {})
        for key, value in values.items():
            agent_bucket[key] = str(int(agent_bucket.get(key, "0")) + int(value))
        self._agent_last_tokens[agent_name] = dict(values)

        # (agent, operation_kind) 维度累计
        kind_bucket = self._agent_tokens_by_kind.setdefault(agent_name, {}).setdefault(
            operation_kind, {}
        )
        for key, value in values.items():
            kind_bucket[key] = str(int(kind_bucket.get(key, "0")) + int(value))
        self._agent_last_tokens_by_kind.setdefault(agent_name, {})[operation_kind] = dict(values)

        self._last_agent_name = agent_name
        self._last_api_tokens = dict(values)

        # 推给记忆 sink
        sink = self._agent_memoried.get(agent_name)
        if sink is not None:
            set_last = getattr(sink, "set_last_api_token", None)
            if set_last is not None:
                set_last(
                    input_token=int(prompt_tokens),
                    output_tokens=int(completed_tokens),
                )

    def set_last_api_token(self, *, input_token: int, output_tokens: int) -> None:
        """记录最近一次 API 用量（不带 agent 维度的简化入口）。

        这是 :class:`MemoryTokenSink` 协议声明的同名方法，
        用于「只关心最近一次」的调用方。
        """
        values = {
            "prompt_tokens": str(int(input_token)),
            "completed_tokens": str(int(output_tokens)),
            "reasoning_tokens": "0",
            "cached_tokens": "0",
            "total_tokens": str(int(input_token) + int(output_tokens)),
        }
        if self._last_agent_name:
            self._agent_last_tokens[self._last_agent_name] = dict(values)
        self._last_api_tokens = values

    # ------------------------------------------------------------------
    # 记忆 sink 登记
    # ------------------------------------------------------------------

    def register_memory_sink(self, agent_name: str, sink: MemoryTokenSink) -> None:
        """为某个 agent 登记记忆 sink。"""
        if agent_name and sink is not None:
            self._agent_memoried[agent_name] = sink

    def unregister_memory_sink(self, agent_name: str) -> None:
        """注销某个 agent 的记忆 sink。"""
        self._agent_memoried.pop(agent_name, None)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_agent_tokens(self, agent_name: str) -> typing.Dict[str, int]:
        """取某个 agent 的累计用量。"""
        bucket = self._agent_tokens.get(agent_name, {})
        return {key: int(value) for key, value in bucket.items()}

    def get_agent_last_tokens(self, agent_name: str) -> typing.Dict[str, int]:
        """取某个 agent 最近一次的用量。"""
        bucket = self._agent_last_tokens.get(agent_name, {})
        return {key: int(value) for key, value in bucket.items()}

    def get_agent_tokens_by_kind(
        self, agent_name: str, operation_kind: str = "normal"
    ) -> typing.Dict[str, int]:
        """取某个 agent 在指定操作类型下的累计用量。"""
        bucket = self._agent_tokens_by_kind.get(agent_name, {}).get(operation_kind, {})
        return {key: int(value) for key, value in bucket.items()}

    def get_agent_last_tokens_by_kind(
        self, agent_name: str, operation_kind: str = "normal"
    ) -> typing.Dict[str, int]:
        """取某个 agent 在指定操作类型下最近一次的用量。"""
        bucket = self._agent_last_tokens_by_kind.get(agent_name, {}).get(operation_kind, {})
        return {key: int(value) for key, value in bucket.items()}

    def get_all_agent_tokens(self) -> typing.Dict[str, typing.Dict[str, int]]:
        """取所有 agent 的累计用量。"""
        return {
            agent: {key: int(value) for key, value in bucket.items()}
            for agent, bucket in self._agent_tokens.items()
        }

    def reset(self) -> None:
        """清空所有累计数据。"""
        self._agent_tokens.clear()
        self._agent_last_tokens.clear()
        self._agent_tokens_by_kind.clear()
        self._agent_last_tokens_by_kind.clear()
        self._agent_memoried.clear()
        self._last_agent_name = ""
        self._last_api_tokens = {}


__all__ = ["MemoryTokenSink", "TokenCounter"]
