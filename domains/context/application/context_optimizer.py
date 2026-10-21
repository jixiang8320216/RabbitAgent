"""上下文优化器：用小模型把冗长的提示词压短。

优化器是**旁路能力** —— 它不属于任何一次具体调用，而是装配期就绪、
在构造系统提示词时按需调用的辅助组件。

**优化什么**：系统提示词往往很长（技能说明、环境信息、历史摘要），
而每次请求都要重发一遍。优化器让模型把这些内容改写成更紧凑的表述，
在保持信息量的前提下减少 token 消耗。

**失败语义**：优化失败**必须回落原文**。提示词优化是锦上添花，
不该让主链路因为「优化器挂了」而无法启动或回复。

**历史缺口**：本模块此前是 0 字节文件，而
``context_optimizer_adapter`` 会构造 ``ContextOptimizer``，
``system_prompt_builder`` 又调用 ``optimize(prompt)`` ——
上下文优化链路整体不可用。这里按调用契约补齐。
"""

from __future__ import annotations

import logging
import typing

logger = logging.getLogger(__name__)


#: 优化指令模板。{content} 是待优化的原文。
_OPTIMIZE_INSTRUCTION = (
    "请压缩下面的系统提示词，在**不丢失任何约束、规则与事实**的前提下"
    "让它尽可能简洁。保留关键指令、工具名称、路径与格式要求；"
    "删掉重复表述与冗余修饰。只输出压缩后的提示词本身，"
    "不要加任何解释或前缀。\n\n"
    "待压缩内容：\n{content}"
)


class ContextOptimizer:
    """基于模型的提示词压缩器。"""

    def __init__(
        self,
        *,
        model: typing.Any,
        max_input_chars: int = 20_000,
        max_output_chars: int = 8_000,
    ) -> None:
        """
        :param model: 文本进文本出的模型（通常是
            ``ChatModelContextOptimizationAdapter``）
        :param max_input_chars: 输入上限；超长则直接跳过优化
        :param max_output_chars: 输出上限；超长则截断
        """
        self._model = model
        self._max_input_chars = int(max_input_chars)
        self._max_output_chars = int(max_output_chars)

    @property
    def model(self) -> typing.Any:
        """底层模型。"""
        return self._model

    async def optimize(self, prompt: str) -> str:
        """优化一段提示词。

        :param prompt: 原始提示词
        :return: 优化后的提示词；**任何失败都返回空串**，
            调用方据此回落原文。
        """
        if not prompt or not prompt.strip():
            return ""
        if len(prompt) > self._max_input_chars:
            logger.debug(
                "context optimize skipped: prompt %d chars exceeds limit %d",
                len(prompt),
                self._max_input_chars,
            )
            return ""

        instruction = _OPTIMIZE_INSTRUCTION.format(content=prompt)
        try:
            result = self._model.generate(instruction)
            if hasattr(result, "__await__"):
                result = await result
        except Exception:  # noqa: BLE001 - 优化失败不该阻断主流程
            logger.debug("context optimize failed", exc_info=True)
            return ""

        if not isinstance(result, str):
            return ""

        optimized = result.strip()
        if not optimized:
            return ""

        if len(optimized) > self._max_output_chars:
            optimized = optimized[: self._max_output_chars]

        # 压缩后不该比原文还长 —— 那说明模型没在压缩，回落原文
        if len(optimized) >= len(prompt):
            logger.debug(
                "context optimize produced no reduction (%d -> %d), keeping original",
                len(prompt),
                len(optimized),
            )
            return ""

        return optimized


__all__ = ["ContextOptimizer"]
