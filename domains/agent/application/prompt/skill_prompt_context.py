"""技能提示词上下文：把「这个 agent 能用哪些技能」渲染成提示词段落。

职责
----
``SystemPromptBuilder`` 在拼系统提示词时，需要告诉模型两件关于技能的事：

1. **有哪些技能**（摘要）——:func:`build_skill_summary_context`；
2. **技能在磁盘上的什么位置**（运行时上下文）——:func:`build_skill_runtime_context`。

两者刻意分开，且都只给**摘要而非正文**。这是渐进式披露（progressive
disclosure）：技能正文动辄上千 token，全量注入所有 agent 的提示词会让上下文
被技能说明挤爆；正确做法是提示词里只留「名字 + 一句话描述 + 去哪读全文」，
模型判断相关时再按需读取完整文件。

调用方：``SystemPromptBuilder._append_creation_context`` 把这两个函数包成
``provider`` 传给 ``static_environment_context``；那边已经做了兜底——本模块抛
任何异常都只会导致对应段落缺席，不会让 agent 装配失败。因此这里**不需要**
再包一层 try/except 去吞异常：让问题暴露在日志里，好过静默返回空串。

数据来源的两个分支
------------------
技能名来自 ``AgentRuntime`` 的声明（``skill_refs`` / ``package_skill_names`` /
``skill_name``），描述与路径来自 ``SkillRegistry``。两者可能不一致——声明里有、
注册表里没有，说明**配置漂移**（技能包被删了、目录没被扫到、名字写错）。这种
情况不下断言、也不静默跳过，而是在输出里显式标注「未注册」：提示词是给人看的
诊断材料，让漂移可见比让它安静更重要。
"""

from __future__ import annotations

import logging
import pathlib
import typing

logger = logging.getLogger(__name__)

#: 单个技能目录下最多列出的附带资源数量。
#:
#: 只列一层、且限量——技能目录里可能有大量数据文件，全列出来既无意义又撑爆上下文。
_MAX_LISTED_SIBLINGS = 20

#: 列目录时跳过的后缀（编译产物）。
_SKIP_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})

#: 技能摘要段标题。与 ``static_environment_context`` 的 ``# STATIC ENVIRONMENT``
#: 保持同一套「大写标题」风格，便于模型区分提示词的各个段落。
_SUMMARY_HEADER = "# AVAILABLE SKILLS"

_RUNTIME_HEADER = "# SKILL RUNTIME CONTEXT"

#: 注册表里查不到时的标注文案。
_UNREGISTERED_MARK = "（未注册）"


def build_skill_summary_context(
    runtime_agent: typing.Any,
    skill_registry: typing.Any = None,
) -> str:
    """渲染「可用技能」摘要段：名字 + 一句话描述。

    :param runtime_agent: ``AgentRuntime`` 实例（鸭子类型，只取技能相关字段）。
    :param skill_registry: ``SkillRegistry``；为 ``None`` 时只输出技能名。
    :return: 提示词段落；**没有任何技能声明时返回空串**，由调用方决定是否拼接。
    """
    names = _collect_skill_names(runtime_agent)
    if not names:
        return ""

    registry = _ensure_loaded(skill_registry)
    lines: list[str] = [_SUMMARY_HEADER, ""]
    for name in names:
        package = registry.get(name) if registry is not None else None
        if package is None:
            lines.append(f"- {name}{_UNREGISTERED_MARK}")
            continue
        description = str(getattr(package, "description", "") or "").strip()
        suffix = f" —— {description}" if description else ""
        lines.append(f"- {name}{suffix}")
    return "\n".join(lines)


def build_skill_runtime_context(
    runtime_agent: typing.Any,
    skill_registry: typing.Any = None,
) -> str:
    """渲染技能运行时上下文：技能在磁盘上的位置与附带资源。

    与摘要段的分工：摘要回答「有什么」，本段回答「去哪拿」。模型看到
    ``清单: <path>`` 后可以按需读取完整技能说明，而不必在提示词里常驻全文。

    :param runtime_agent: 同 :func:`build_skill_summary_context`。
    :param skill_registry: 同 :func:`build_skill_summary_context`。
    :return: 提示词段落；技能名与技能目录都没有时返回空串。
    """
    sections: list[str] = []

    # agent 自带的技能目录优先——它是「这个 agent 就是由某个技能变来的」的直接证据
    skill_dir = getattr(runtime_agent, "skill_dir", None)
    if skill_dir is not None:
        sections.append(f"- 本 agent 技能目录：{skill_dir}")

    names = _collect_skill_names(runtime_agent)
    registry = _ensure_loaded(skill_registry)
    for name in names:
        package = registry.get(name) if registry is not None else None
        if package is None:
            # 运行时上下文只讲「有据可查的位置」，未注册的技能无路径可给，
            # 这里直接跳过——「未注册」这件事已经在摘要段标出来了，不重复。
            continue

        manifest = pathlib.Path(getattr(package, "path", ""))
        block: list[str] = [f"## {getattr(package, 'name', name)}"]
        description = str(getattr(package, "description", "") or "").strip()
        if description:
            block.append(description)
        block.append(f"- 清单：{manifest}")
        block.append(f"- 目录：{manifest.parent}")
        siblings = _sibling_files(manifest)
        if siblings:
            block.append(f"- 附带资源：{', '.join(siblings)}")
        sections.append("\n".join(block))

    if not sections:
        return ""
    return f"{_RUNTIME_HEADER}\n\n" + "\n\n".join(sections)


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------

def _collect_skill_names(runtime_agent: typing.Any) -> list[str]:
    """收集 agent 声明的技能名：去重，且**保持声明顺序**。

    三个来源的优先级即遍历顺序：``skill_refs``（显式引用，最权威）→
    ``package_skill_names``（随技能包带入）→ ``skill_name``（单技能 agent）。

    用 ``getattr`` 逐项取而不是直接点属性：``runtime_agent`` 在生产上是
    ``AgentRuntime``，但测试与其它调用方可能传入鸭子类型的替身，缺字段应当
    退化成「没有该来源」而不是 ``AttributeError``。
    """
    names: list[str] = []
    seen: set[str] = set()

    def _add(value: typing.Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            names.append(text)

    for ref in getattr(runtime_agent, "skill_refs", None) or []:
        _add(ref)
    for name in getattr(runtime_agent, "package_skill_names", None) or []:
        _add(name)
    _add(getattr(runtime_agent, "skill_name", None))
    return names


def _ensure_loaded(registry: typing.Any) -> typing.Any:
    """确保注册表已扫描；拿不到或形态不符时返回 ``None``。

    注册表构造后不会自动扫描（``SkillRegistry.load`` 是显式调用）。这里兜一次
    幂等的 ``load()``——它内部有缓存，已加载时会直接返回，不会重复扫盘。
    """
    if registry is None or not hasattr(registry, "get"):
        return None
    if not hasattr(registry, "load"):
        return registry

    try:
        is_empty = len(registry) == 0
    except Exception:  # noqa: BLE001 - __len__ 抛错时按「未加载」处理
        is_empty = True
    if not is_empty:
        return registry

    try:
        registry.load()
    except Exception:  # noqa: BLE001 - 扫盘失败不应阻断提示词装配
        logger.debug("技能注册表加载失败，技能描述将缺席", exc_info=True)
        return None
    return registry


def _sibling_files(manifest: pathlib.Path) -> list[str]:
    """列出技能目录下除清单外的附带文件（只一层、限量）。

    目的是让模型知道这个技能包里还有哪些脚本/模板可参考；不做递归——深层目录
    结构属于技能正文该自己说明的内容。
    """
    try:
        entries = sorted(
            path for path in manifest.parent.iterdir() if path.is_file()
        )
    except (OSError, ValueError):
        return []

    names = [
        path.name
        for path in entries
        if path.name != manifest.name and path.suffix not in _SKIP_SUFFIXES
    ]
    return names[:_MAX_LISTED_SIBLINGS]


__all__ = [
    "build_skill_summary_context",
    "build_skill_runtime_context",
]
