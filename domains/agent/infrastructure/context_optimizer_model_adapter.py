"""上下文优化器与 ChatModel 之间的适配层。

优化器需要「发一段纯文本、拿回一段纯文本」，而 ``ChatModel`` 的接口是
「发消息列表、流出 ChatResponse」。两者形状不同，适配器负责转换。

**为什么要单独一层**：优化器属于「上下文」领域的知识，不该知道
ChatModel 的流式协议；而 ``ChatModel`` 也不该知道有个优化器。
适配器是两者之间唯一的耦合点。

**历史缺口**：本模块此前是 0 字节文件，而
``context_optimizer_adapter`` 会构造 ``ChatModelContextOptimizationAdapter``，
导致上下文优化在装配阶段就 AttributeError。
"""

from __future__ import annotations

import logging
import typing

import domains.agent.domain.aggregates.chat_model as chat_model

logger = logging.getLogger(__name__)


class ChatModelContextOptimizationAdapter:
    """把 ``ChatModel`` 适配成「文本进、文本出」的优化器模型。"""

    def __init__(self, model: chat_model.ChatModel) -> None:
        self._model = model

    @property
    def model(self) -> chat_model.ChatModel:
        """底层模型。"""
        return self._model

    async def generate(self, prompt: str) -> str:
        """发一段提示词，取回纯文本回复。

        :param prompt: 待处理的提示词
        :return: 模型回复文本；出错时返回空串（调用方据此回落原提示词）
        """
        if not prompt or not prompt.strip():
            return ""

        messages: list[dict[str, typing.Any]] = [
            {"role": "user", "content": prompt},
        ]

        chunks: list[str] = []
        try:
            async for response in self._model(messages):
                text = getattr(response, "content", None)
                if isinstance(text, str):
                    chunks.append(text)
                elif isinstance(text, list):
                    # content 可能是内容块列表，逐块取文本
                    for block in text:
                        if isinstance(block, str):
                            chunks.append(block)
                        elif isinstance(block, dict):
                            piece = block.get("text")
                            if isinstance(piece, str):
                                chunks.append(piece)
        except Exception:  # noqa: BLE001 - 优化失败不该阻断主流程
            logger.debug("context optimization model call failed", exc_info=True)
            return ""

        return "".join(chunks).strip()


__all__ = ["ChatModelContextOptimizationAdapter"]
