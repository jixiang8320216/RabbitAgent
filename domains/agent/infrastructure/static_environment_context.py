"""静态环境上下文：把「不随对话变化的运行事实」拼进系统提示词。

职责
----
一个 agent 的提示词由三部分组成：模式/角色提示词（由 ``SystemPromptBuilder``
拼）、附加段落（工具、专家、记忆等）、以及本模块负责的**静态环境事实**——
工作目录、操作系统、日期这类每次都一样、但对模型定位很有用的信息。

对外只有两个函数（调用方见 ``agent.service.prompt_builder``）：

- :func:`append_static_agent_context` —— 拼环境块 + 技能摘要；
- :func:`append_prompt_sections` —— 把若干附加段落追加到提示词末尾。

绝不抛异常
----------
提示词是 agent 的装配素材，拼装失败只能降级，不能让 agent 装配整体失败。因此
两个 provider（技能摘要 / 技能运行时上下文）的调用都做了兜底：它们依赖的
``agent.service.skill_prompt_context`` 目前还是空文件，取不到是常态。
"""

from __future__ import annotations

import datetime as datetime_module
import logging
import os
import platform
import typing

logger = logging.getLogger(__name__)

# 段落之间的分隔符：不参与行内排版，仅用于把多段提示词隔开
SECTION_SEPARATOR = "\n\n---\n\n"

_ENVIRONMENT_HEADER = "# STATIC ENVIRONMENT"


def append_prompt_sections(
    prompt: str,
    sections: typing.Iterable[str] | None,
) -> str:
    """把 ``sections`` 里的非空段落依次追加到 ``prompt`` 末尾。

    没有可用段落时原样返回 ``prompt``（不改变调用方的既有内容）。
    """
    if not sections:
        return prompt
    parts: list[str] = [prompt] if prompt else []
    for section in sections:
        if not section:
            continue
        text = str(section).strip()
        if text:
            parts.append(text)
    if len(parts) == 1:
        return prompt
    return SECTION_SEPARATOR.join(parts)


def append_static_agent_context(
    system_prompt: str,
    *,
    agent_name: str,
    skill_summary_provider: typing.Callable[[], str] | None = None,
    skill_runtime_context_provider: typing.Callable[[], str] | None = None,
    project_home: str | None = None,
    session_id: str | None = None,
) -> str:
    """在提示词后追加环境块与技能上下文。

    :param system_prompt: 已拼好的基础提示词。
    :param agent_name: agent 名，写进环境块便于日志与多路复用时区分。
    :param skill_summary_provider: 返回技能摘要的回调；取不到就整段跳过。
    :param skill_runtime_context_provider: 返回技能运行时上下文的回调，同上。
    :param project_home: 项目根目录；为空时退回当前工作目录。
    :param session_id: 当前会话标识，仅作记录。
    """
    environment = _build_environment_block(
        agent_name=agent_name,
        project_home=project_home,
        session_id=session_id,
    )
    sections: list[str] = [environment] if environment else []

    skill_summary = _safe_provider(skill_summary_provider, "skill summary")
    if skill_summary:
        sections.append(skill_summary)

    skill_runtime = _safe_provider(skill_runtime_context_provider, "skill runtime context")
    if skill_runtime:
        sections.append(skill_runtime)

    return append_prompt_sections(system_prompt, sections)


def _build_environment_block(
    *,
    agent_name: str,
    project_home: str | None,
    session_id: str | None,
) -> str:
    """拼环境事实块；任何一项取不到就退化为省略该项。"""
    lines = [_ENVIRONMENT_HEADER, ""]
    if agent_name:
        lines.append(f"- agent: {agent_name}")
    lines.append(f"- working directory: {project_home or _current_directory()}")
    lines.append(f"- platform: {_platform_text()}")
    lines.append(f"- date: {datetime_module.date.today().isoformat()}")
    if session_id:
        lines.append(f"- session id: {session_id}")
    return "\n".join(lines)


def _current_directory() -> str:
    try:
        return os.getcwd()
    except Exception:  # noqa: BLE001 - cwd 被删除时 os.getcwd() 会抛
        logger.debug("failed to resolve current working directory", exc_info=True)
        return "."


def _platform_text() -> str:
    try:
        return f"{platform.system()} {platform.release()}"
    except Exception:  # noqa: BLE001
        logger.debug("failed to resolve platform", exc_info=True)
        return "unknown"


def _safe_provider(provider: typing.Callable[[], str] | None, label: str) -> str:
    """调用 provider 并规整结果；缺席、抛异常、非字符串一律当作「没有」。

    技能上下文属于增强信息，拿不到就整段不拼——绝不能让一个空模块把整条
    提示词装配链路打断。
    """
    if provider is None:
        return ""
    try:
        value = provider()
    except Exception:  # noqa: BLE001
        logger.debug("failed to build %s", label, exc_info=True)
        return ""
    if not value:
        return ""
    return str(value).strip()
