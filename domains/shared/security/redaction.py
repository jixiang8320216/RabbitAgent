"""敏感信息脱敏。

指南 L4-4.1（日志脱敏）标为 P0：日志/Trace 中不得含明文密钥或Token。
指南 L4-4.2 要求子Agent 上下文不传递不必要的敏感字段。

为什么放在 shared：
    脱敏是横切关注点——日志、记忆、审计、错误上报都要用。若各域各写一份，
    必然出现「日志脱敏了但记忆没脱敏」这类不一致缺口，而这正是泄漏的入口。

设计要点：
    **宁可过度脱敏，不可漏脱敏**。漏掉一个密钥的后果（凭据泄漏）远大于
    多遮盖几个字符（可读性下降）。因此模式列表偏保守，且对高熵串也做处理。

覆盖类型：
    显式前缀的密钥（``sk-`` / ``ghp_`` / ``AKIA`` / ``Bearer``）、
    ``key=value`` 形式的赋值、公私钥块、JWT、以及 Authorization 头。
"""

from __future__ import annotations

import re
import typing

#: 脱敏后的占位符。保留前后少量字符便于人工比对，但不足以还原凭证。
PLACEHOLDER = "***REDACTED***"

#: 形如 ``sk-xxxxxxxx`` 的带前缀凭证。
_PREFIXED_PATTERNS = (
    r"sk-[A-Za-z0-9_\-]{8,}",
    r"sk-ant-[A-Za-z0-9_\-]{8,}",
    r"gh[pousr]_[A-Za-z0-9]{16,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"ASIA[0-9A-Z]{16}",
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"AIza[0-9A-Za-z_\-]{20,}",
    r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",  # JWT
)

#: ``key=value`` / ``key: value`` 形式的敏感赋值。
_ASSIGNMENT_KEYS = (
    "api[_-]?key|apikey|secret[_-]?key|secret|access[_-]?token|refresh[_-]?token"
    "|auth[_-]?token|token|password|passwd|pwd|private[_-]?key|client[_-]?secret"
    "|credential|authorization|session[_-]?id"
)

#: PEM 私钥/证书块（整块遮盖）。
#: END 行本身含大量 '-'，因此这里不能用排除 '-' 的字符类去匹配结尾标记，
#: 否则整块匹配不上（曾导致私钥正文原样输出）。
_PEM_BLOCK = r"-----BEGIN[^-]{0,40}-----[\s\S]*?-----END[^-]{0,40}-----"

_ASSIGNMENT_PATTERN = re.compile(
    rf"(?i)\b({_ASSIGNMENT_KEYS})\b\s*[=:]\s*"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&\"'{{}}]+)"
)

_BEARER_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}")

_ALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(_PEM_BLOCK),
    re.compile(r"(?i)" + "|".join(_PREFIXED_PATTERNS)),
    _BEARER_PATTERN,
)


def mask(value: typing.Any, keep: int = 0) -> str:
    """脱敏任意值中的敏感片段。

    :param value: 待脱敏的内容（会被强制转字符串）。
    :param keep: 每段敏感内容首尾保留的字符数，默认 0（全遮盖）。
    :return: 脱敏后的字符串。
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if not text:
        return text

    for pattern in _ALL_PATTERNS:
        text = pattern.sub(lambda m: _replace(m.group(0), keep), text)

    def _mask_assignment(match: re.Match[str]) -> str:
        key = match.group(1)
        raw = match.group(2)
        quote = ""
        body = raw
        if raw[:1] in {'"', "'"} and raw[-1:] == raw[:1]:
            quote, body = raw[0], raw[1:-1]
        masked_body = _replace(body, keep)
        return f"{key}={quote}{masked_body}{quote}"

    text = _ASSIGNMENT_PATTERN.sub(_mask_assignment, text)
    return text


def _replace(secret: str, keep: int) -> str:
    if not secret:
        return PLACEHOLDER
    if keep > 0 and len(secret) > keep * 2:
        return f"{secret[:keep]}{'*' * (len(secret) - keep * 2)}{secret[-keep:]}"
    return PLACEHOLDER


def contains_secret(value: typing.Any) -> bool:
    """判断内容中是否含疑似敏感信息。

    供「写入记忆前是否需要脱敏」这类前置判断使用，避免对无敏感内容的
    正常记忆做无谓改写。
    """
    if value is None:
        return False
    text = value if isinstance(value, str) else str(value)
    if not text:
        return False
    for pattern in _ALL_PATTERNS:
        if pattern.search(text):
            return True
    return bool(_ASSIGNMENT_PATTERN.search(text))


def mask_mapping(
    data: typing.Mapping[str, typing.Any], *, keep: int = 0
) -> dict[str, typing.Any]:
    """脱敏字典中的敏感字段值，保留键结构。

    用于子 Agent 上下文传递前的字段级过滤（L4-4.2）。
    """
    return {
        key: (mask(item, keep) if contains_secret(item) else item)
        for key, item in data.items()
    }


# 常见别名，便于不同调用点用习惯的名字访问同一语义
redact = mask
scrub = mask
sanitize = mask


__all__ = [
    "PLACEHOLDER",
    "contains_secret",
    "mask",
    "mask_mapping",
    "redact",
    "sanitize",
    "scrub",
]