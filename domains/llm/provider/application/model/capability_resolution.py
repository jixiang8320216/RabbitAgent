"""模型能力识别：根据模型名判断其是否具备某种「思考 / 推理」能力。

这些是轻量启发式（基于模型名子串），仅用于请求策略路由与流式格式化选择；
真正的模型能力仍以各 provider 文档 / 配置为准。
"""

import enum
import typing
import re

_KIMI_K3_MODEL_PATTERN= re.compile(
    r"(?<![a-z0-9])kimi[-_]?k3(?=[-_/]|$)]",
    re.IGNORECASE,
)


def is_deepseek_model(model_name: str) -> bool:
    return "deepseek" in model_name.casefold()


def _is_kimi_k3_model(model_name: str) -> bool:
    return _KIMI_K3_MODEL_PATTERN.search(model_name.strip()) is not None


# 公开别名，保持与 `_is_kimi_k3_model` 语义一致。
def is_kimi_k3_model(model_name: str) -> bool:
    return _is_kimi_k3_model(model_name)


def is_glm_thinking_model(model_name: str) -> bool:
    return "glm" in model_name.casefold()


def is_qwen_thinking_model(model_name: str) -> bool:
    name = model_name.casefold()
    return "qwen" in name or "qwq" in name


def is_minimax_thinking_model(model_name: str) -> bool:
    name = model_name.casefold()
    return "minimax" in name or "abab" in name


class QwenThinkingCapability(enum.Enum):
    """Qwen 系列模型的 thinking 能力形态。

    - ``THINKING_ONLY``：始终推理、无法关闭（如 QwQ 系列），请求里不能带 ``enable_thinking``；
    - ``HYBRID``：支持 ``enable_thinking`` 开关（Qwen3 及之后）；
    - ``NONE``：不支持思考开关的老型号（如 Qwen2.x / Qwen-Max）。
    """

    NONE = "none"
    HYBRID = "hybrid"
    THINKING_ONLY = "thinking_only"


# QwQ 是「推理专用」系列：不可关闭思考。
_QWEN_THINKING_ONLY_PATTERN = re.compile(
    r"(?<![a-z0-9])qwq(?![a-z0-9])",
    re.IGNORECASE,
)

# 形如 qwen3 / qwen3.5 / qwen3.8 —— 提取主版本号判断是否支持开关。
_QWEN_VERSION_PATTERN = re.compile(
    r"(?<![a-z0-9])qwen(?P<major>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def get_qwen_thinking_capability(model_name: str) -> QwenThinkingCapability:
    """判断某个模型的 Qwen thinking 能力形态。"""
    name = model_name.casefold().replace("/", "-").strip()
    if _QWEN_THINKING_ONLY_PATTERN.search(name):
        return QwenThinkingCapability.THINKING_ONLY
    match = _QWEN_VERSION_PATTERN.search(name)
    if match is not None:
        try:
            major = float(match.group("major"))
        except ValueError:  # pragma: no cover - 正则已保证是数字
            return QwenThinkingCapability.NONE
        # Qwen3 起引入可开关的 mixed thinking；Qwen2.x 及更早没有该参数。
        if major >= 3:
            return QwenThinkingCapability.HYBRID
    return QwenThinkingCapability.NONE


def get_qwen_thinking_default(model_name: str) -> bool | None:
    """模型在不显式指定时的 thinking 默认值。

    只有 ``HYBRID`` 形态才有默认开关状况（默认开启）；``THINKING_ONLY`` 与
    ``NONE`` 均返回 ``None``，交由调用方按其语义处理（前者走 thinking-only 分支，
    后者若未被显式启用会由上层抛出明确错误）。
    """
    if get_qwen_thinking_capability(model_name) is QwenThinkingCapability.HYBRID:
        return True
    return None


__all__: typing.Final = [
    "is_deepseek_model",
    "is_kimi_k3_model",
    "_is_kimi_k3_model",
    "is_glm_thinking_model",
    "is_qwen_thinking_model",
    "is_minimax_thinking_model",
    "QwenThinkingCapability",
    "get_qwen_thinking_capability",
    "get_qwen_thinking_default",
]
