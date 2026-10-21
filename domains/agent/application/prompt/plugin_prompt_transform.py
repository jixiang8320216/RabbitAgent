"""插件对系统提示词的变换（``system_transform`` 贡献点）。

``AgentSlot.contribution_policies`` 里可以声明
:attr:`plugin.domain.contribution.ContributionType.SYSTEM_TRANSFRORM`
策略，表示「本槽位的系统提示词允许被插件改写」。本模块就是把这条声明落到
实际调用上的地方。

设计上遵循本项目的降级约定：插件体系没就绪、槽位没开这个开关、或者某个插件
抛异常，都只是**这一段变换不生效**，提示词原样返回，绝不让 agent 装配失败。

变换方法的语义按名字区分：

- ``transform_system_prompt`` / ``transform_prompt`` / ``system_transform``
  返回整串提示词 —— **替换**当前提示词；
- ``build_prompt_section`` / ``prompt_section`` 返回一个片段 —— **追加**到
  当前提示词后面。

两种都支持同步与异步。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import typing

import domains.plugin.domain.contribution as contribution_models

logger = logging.getLogger(__name__)

__all__ = ["append_plugin_system_transform"]


#: 返回整串提示词（替换语义）的方法名，按优先级排列。
_REPLACE_METHODS: tuple[str, ...] = (
    "transform_system_prompt",
    "transform_prompt",
    "system_transform",
)

#: 返回片段（追加语义）的方法名，按优先级排列。
_APPEND_METHODS: tuple[str, ...] = (
    "build_prompt_section",
    "prompt_section",
)

#: 调用变换方法时按需投递的候选关键字。
_CALL_KWARGS: tuple[str, ...] = (
    "system_prompt",
    "prompt",
    "agent_name",
    "name",
    "agent_role",
    "role",
    "slot",
    "contribution_type",
)


async def append_plugin_system_transform(
    system_prompt: str,
    *,
    plugin_manager: typing.Any | None,
    agent_name: str = "",
    agent_role: typing.Any | None = None,
    slot: typing.Any | None = None,
    log_context: str = "",
) -> str:
    """把插件的 ``system_transform`` 贡献应用到系统提示词上。

    :param system_prompt: 当前系统提示词。
    :param plugin_manager: 插件管理器；``None`` 时直接原样返回。
    :param agent_name: 目标 agent 名，供插件按名字定制。
    :param agent_role: 目标 agent 角色（``AgentRole``）。
    :param slot: 目标槽位声明（``AgentSlot``）；其 ``contribution_policies``
        决定本类贡献是否被允许。
    :param log_context: 日志前缀，便于区分调用方。
    :return: 变换后的提示词；无需变换或变换失败时返回入参本身。
    """
    prefix = f"[{log_context}] " if log_context else ""
    if plugin_manager is None:
        logger.debug("%splugin manager unavailable; system prompt unchanged", prefix)
        return system_prompt
    if not _transform_allowed(slot):
        logger.debug(
            "%ssystem_transform contribution not enabled for %r; prompt unchanged",
            prefix,
            agent_name,
        )
        return system_prompt

    disabled = _disabled_plugin_names()
    current = system_prompt or ""
    applied = 0
    for plugin in _iter_transform_plugins(plugin_manager, slot):
        name = _plugin_name(plugin)
        if name and name in disabled:
            logger.debug("%sskip disabled plugin %r", prefix, name)
            continue
        current, changed = await _apply_one(
            plugin,
            current,
            agent_name=agent_name,
            agent_role=agent_role,
            slot=slot,
            prefix=prefix,
        )
        applied += int(changed)
    if applied:
        logger.debug(
            "%sapplied %d system_transform contribution(s) to %r",
            prefix,
            applied,
            agent_name,
        )
    return current


# -- 开关判断 -------------------------------------------------------------


def _transform_allowed(slot: typing.Any) -> bool:
    """槽位是否允许 ``system_transform`` 贡献。

    ``contribution_policies`` 为空表示「未声明」，此时不拦——默认放开，交由
    插件管理器自己决定有没有可用的变换。只有**显式声明了策略但没带上**
    ``SYSTEM_TRANSFRORM`` 才视为关闭。
    """
    if slot is None:
        return True
    policies = getattr(slot, "contribution_policies", None)
    if not policies:
        return True
    try:
        return contribution_models.ContributionType.SYSTEM_TRANSFRORM in policies
    except TypeError:  # 非映射/非集合类型，按未声明处理
        return True


# -- 插件收集 -------------------------------------------------------------


def _iter_transform_plugins(
    manager: typing.Any,
    slot: typing.Any,
) -> list[typing.Any]:
    """收集管理器上所有带变换方法的插件。

    插件管理器的 API 尚未定型，因此按若干常见形态试探；任何一种命中即可，
    全都取不到就返回空列表（等价于「没有插件要变换」）。
    """
    collected: list[typing.Any] = []

    for getter in ("get_system_transforms", "system_transforms"):
        provider = getattr(manager, getter, None)
        if not callable(provider):
            continue
        try:
            produced = provider(slot) if _accepts(provider, "slot") else provider()
        except Exception:  # noqa: BLE001
            logger.debug("system transform provider %r failed", getter, exc_info=True)
            continue
        collected.extend(_flatten(produced))

    for getter in ("get_contributions", "contributions"):
        provider = getattr(manager, getter, None)
        if not callable(provider):
            continue
        try:
            produced = provider(contribution_models.ContributionType.SYSTEM_TRANSFRORM)
        except Exception:  # noqa: BLE001
            logger.debug("contribution provider %r failed", getter, exc_info=True)
            continue
        collected.extend(_flatten(produced))

    for attribute in ("plugins", "active_plugins", "enabled_plugins", "registry", "_plugins"):
        collected.extend(_flatten(getattr(manager, attribute, None)))

    # 去重并保持顺序
    seen: set[int] = set()
    unique: list[typing.Any] = []
    for item in collected:
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return [item for item in unique if _transform_method_of(item) is not None]


def _flatten(value: typing.Any) -> list[typing.Any]:
    """把「对象 / 列表 / 字典 / 元组」摊平成一维列表。"""
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, dict):
        items = list(value.values())
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        items = [value]
    return [item for item in items if item is not None]


def _transform_method_of(plugin: typing.Any) -> tuple[str, typing.Any] | None:
    """取插件上的变换方法；返回 ``(方法名, 可调用对象)``。"""
    for names in (_REPLACE_METHODS, _APPEND_METHODS):
        for name in names:
            method = getattr(plugin, name, None)
            if callable(method):
                return name, method
    return None


# -- 单次应用 -------------------------------------------------------------


async def _apply_one(
    plugin: typing.Any,
    system_prompt: str,
    *,
    agent_name: str,
    agent_role: typing.Any,
    slot: typing.Any,
    prefix: str,
) -> tuple[str, bool]:
    """应用单个插件的变换；返回 ``(新提示词, 是否生效)``。"""
    found = _transform_method_of(plugin)
    if found is None:
        return system_prompt, False
    method_name, method = found
    try:
        result = method(**_build_kwargs(method, system_prompt, agent_name, agent_role, slot))
        if inspect.isawaitable(result):
            result = await result
    except Exception:  # noqa: BLE001
        logger.warning(
            "%splugin %r failed to transform system prompt via %s; skipped",
            prefix,
            _plugin_name(plugin),
            method_name,
            exc_info=True,
        )
        return system_prompt, False

    text = _as_text(result)
    if not text:
        return system_prompt, False
    if method_name in _APPEND_METHODS:
        return _join_section(system_prompt, text), True
    return text, True


def _build_kwargs(
    method: typing.Any,
    system_prompt: str,
    agent_name: str,
    agent_role: typing.Any,
    slot: typing.Any,
) -> dict[str, typing.Any]:
    """只投递目标方法真实接受的关键字，避免签名不匹配直接 TypeError。"""
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return {}
    accepts_var = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    available = {
        "system_prompt": system_prompt,
        "prompt": system_prompt,
        "agent_name": agent_name,
        "name": agent_name,
        "agent_role": agent_role,
        "role": agent_role,
        "slot": slot,
        "contribution_type": contribution_models.ContributionType.SYSTEM_TRANSFRORM,
    }
    if accepts_var:
        return dict(available)
    return {key: value for key, value in available.items() if key in parameters}


def _as_text(value: typing.Any) -> str:
    """把变换结果归一成文本；列表/元组按段落拼接。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "\n\n".join(parts)
    return str(value).strip()


def _join_section(system_prompt: str, section: str) -> str:
    """追加一个片段；已在提示词里出现过则不重复追加。"""
    if not system_prompt:
        return section
    if section in system_prompt:
        return system_prompt
    return f"{system_prompt}\n\n---\n\n{section}"


# -- 杂项 -----------------------------------------------------------------


def _accepts(target: typing.Any, name: str) -> bool:
    """目标可调用对象是否接受名为 ``name`` 的参数。"""
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return False
    if name in parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _plugin_name(plugin: typing.Any) -> str:
    for attribute in ("name", "plugin_name", "id"):
        value = getattr(plugin, attribute, None)
        if isinstance(value, str) and value:
            return value
    return type(plugin).__name__


def _disabled_plugin_names() -> frozenset[str]:
    """本次请求中被禁用的插件名；取不到时为空集。"""
    try:
        import domains.plugin.application.request_context as plugin_request_context
    except Exception:  # noqa: BLE001
        return frozenset()
    try:
        disabled = plugin_request_context.get_request_disabled_plugins()
    except Exception:  # noqa: BLE001
        return frozenset()
    if not disabled:
        return frozenset()
    return frozenset(str(item) for item in disabled)


def __repr__() -> str:  # pragma: no cover - 模块级调试用
    return "plugin_prompt_transform"
