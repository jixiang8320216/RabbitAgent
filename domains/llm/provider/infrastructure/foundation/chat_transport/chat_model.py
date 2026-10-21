"""provider 侧的 ``ChatModel`` 契约。

``ProviderChatModelBase``（同目录 ``provider_chat_model``）以它为基类，各
provider family（anthropic / azure_openai / openai_codex …）的具体
``*ChatModel`` 再继承 ``ProviderChatModelBase``。

它同时继承 ``runtime.service.chart_model.ChartModel``——主循环对 model 的
类型声明就是这个抽象类，继承它才能保证 ``isinstance(model, ChartModel)``
成立（``LLMModelAccessService`` 的模型缓存、``ReActAgent`` 的 ``model``
参数都按这个契约走）。

这里只声明形状，**不实现任何 HTTP 调用**：抽象方法 ``__call__`` 留给具体
family 实现。
"""

from __future__ import annotations

import abc
import typing

import domains.agent.domain.aggregates.chat_model as base_chart_model
import domains.agent.domain.value_objects.chat_response as chat_response

JsonObject = dict[str, typing.Any]


class ChatModel(base_chart_model.ChatModel):
    """所有 provider ``ChatModel`` 的公共基类。"""

    model_name: str = ""
    agent_name: str = ""
    llm_provider: str | None = None
    stream: bool = True
    observer: typing.Any = None

    @property
    def output_token_budget(self) -> int:
        """留给子类按 model profile 覆盖；默认 0（不限制）。"""
        return 0

    async def __call__(
        self,
        messages: list[JsonObject],
        tools: list[JsonObject] | None = None,
        tool_choice: str | JsonObject | None = None,
        **kwargs: typing.Any,
    ) -> chat_response.ChatResponse | str:
        """发一次请求。形状与 ``runtime.service.chart_model.ChatModel`` 一致
        （返回单个 ``ChatResponse`` 或纯文本，由主循环归一化）。"""
        raise NotImplementedError(
            f"{type(self).__name__} does not implement __call__; "
            "the provider family is not wired to a transport yet."
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"{type(self).__name__}(agent_name={self.agent_name!r}, "
            f"model_name={self.model_name!r}, provider={self.llm_provider!r})"
        )
