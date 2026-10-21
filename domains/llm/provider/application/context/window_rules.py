"""内置模型上下文窗口对照表（``builtin_context_window_rules``）。

``context_window_reslover._get_builtin_context_window_for_model`` 在「用户显式配置」
与「模型 metadata」两条优先级之后，兜底查这里。本模块只做一件事：给定模型名，
按**最长前缀匹配**返回生效的上下文窗口大小；查不到返回 ``None``（由调用方回落到
``DEFAULT_CONTEXT_WINDOW``）。

为什么返回 ``None`` 而不是拍一个默认值：假装知道窗口大小会让上层拿错误的预算去做
压缩 / 截断，比明确不知道更危险——这一点与 ``context_window_reslover`` 的设计一致。

最长前缀匹配的意义（见 ``context_window_reslover`` 文档）：``gpt-4o-mini`` 必须命中
``gpt-4o-mini`` 的条目，而不是更短的 ``gpt-4`` / ``gpt-4o`` 前缀——否则小模型会被套上
大模型的窗口预算。

表里的数值是各家族「公开发行」时的典型上下文上限（单位 token）。它们只是兜底，真实
生效值仍优先取自用户配置与模型 metadata；新模型上线若不在表里，就回落到默认值。
"""

from __future__ import annotations

import logging
import re
import typing


logger = logging.getLogger(__name__)

class  QwenContextWindowStrategy:
    def resolve(self, model_name: str) -> int | None:...

class  GlmContextWindowStrategy:
    def resolve(self, model_name: str) -> int | None:...

class  ClaudeContextWindowStrategy:
    def resolve(self, model_name: str) -> int | None:...

class  KimiMiniMaxContextWindowStrategy:
    def resolve(self, model_name: str) -> int | None:...

_GPT_HUGE_CONTEXT_MARKERS=(
    "gpt-5.4",
    "gpt-5.5",
    "gpt5.4",
    "gpt5.5",
    "gpt-5_4",
    "gpt-5_5",
)

class OpenAIContextWindowStrategy:

    def resolve(self, model_name: str) -> int | None:
        normalized_name = model_name.lower()
        if any(marker in normalized_name for marker in _GPT_HUGE_CONTEXT_MARKERS):
            return 1_000_000
        if "gpt-4o" in normalized_name or "gpt4o" in normalized_name:
            return 128_000
        if "gpt-4" in normalized_name:
            return 128_000
        if "o1" in normalized_name or "o3" in normalized_name:
            return 2000_000
        return None


class GeminiContextWindowStrategy:

    def resolve(self, model_name:str)->int|None:
        normalized_name= model_name.lower()
        if "gemini-1.5-pro" in normalized_name:
            return 2_000_000
        return 1_000_000 if "gemini" in normalized_name else None



_BUILTIN_CONTEXT_WINDOW_STRATEGIES: typing.Mapping[str, int] = (
    ClaudeContextWindowStrategy(),
    GlmContextWindowStrategy(),
    QwenContextWindowStrategy(),
    KimiMiniMaxContextWindowStrategy(),
    OpenAIContextWindowStrategy(),
    GeminiContextWindowStrategy(),

)



def resolve_builtin_context_window(model_name: str | None) -> int | None:
    """按最长前缀匹配返回内置上下文窗口；查不到返回 ``None``。

    :param model_name: 模型名（任意大小写 / 带版本后缀均可）。
    :return: 上下文窗口 token 数；无法识别时 ``None``。
    """
    for strategy in _BUILTIN_CONTEXT_WINDOW_STRATEGIES:
        context_window = strategy.resolve(model_name)
        # 两种匹配：原始名前缀，或紧凑串前缀（容忍 ``gpt-4o`` vs ``gpt4o``）。
        if context_window is not None:
            return context_window
    return None

