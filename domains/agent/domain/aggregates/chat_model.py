"""模型调用方（ChartModel / ChatModel）。

``ReActAgent`` 的 ``model`` 参数在类型上声明为
``runtime.service.chart_model.ChartModel``，而主循环真正依赖的只有一件事：

    **它是可调用的，接收 ``messages``（可带 ``tools``），返回能被归一化成
    ``Msg`` 的产物。**

``ReactReplyOrchestrator._call_model`` 会按签名探测去试调用
（``(messages, tools)`` / ``messages=`` / ``msgs=`` …），所以这里把常用的入口
名字都挂上（``__call__`` / ``ainvoke`` / ``invoke`` / ``chat`` / ``complete``），
保证任何一种探测形状都能命中。

真实的 HTTP 调用走 ``llm.llm.LLM``（OpenAI 兼容接口）。它的 ``invoke`` 是
**同步阻塞**的，直接在事件循环里调会把整个 TUI 卡死，所以一律用
``asyncio.to_thread`` 挪到线程池执行。

没有可用后端（``LLM`` 缺失、或凭据不全）时，构造不会抛异常——这是刻意的：
``ModeAgentCreationService`` 在装配期就会造 agent，缺模型不该让启动失败，
而应该在**首次真正要回复时**给出带上下文的明确报错。
"""

from __future__ import annotations

import asyncio
import logging
import typing
import abc

import pydantic

import domains.agent.domain.aggregates.runtime_observer as runtime_observer
import domains.agent.domain.value_objects.chat_response as chat_response

logger = logging.getLogger(__name__)

JsonObject =dict[str, pydantic.JsonValue]


class ChatModel(abc.ABC):

    stream:bool = True
    agent_name:str=""
    model_name:str=""
    observer: runtime_observer.RuntimeObserver

    @property
    def output_token_budget(self)->int:
        return 0

    @abc.abstractmethod
    async def __call__(
        self,
        messages: list[JsonObject],
        tools: list[JsonObject] | None = None,
        tool_choice:str|JsonObject|None = None,
        **kwargs: pydantic.JsonValue,
    ) -> typing.AsyncGenerator[chat_response.ChatResponse,None]:
        pass


