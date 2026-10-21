"""角色上下文应用服务。

角色上下文（role context）记录「某个 agent 当前扮演什么角色」，是 roleplay
模式与多 agent 协作的基础设施。

本服务是应用层薄封装：把「角色上下文的变更」与「变更后必须发生的副作用」
绑在一起。调用点（``rabbit_application.py:61``）注入了五组依赖，各自职责：

- ``role_context``：角色上下文存储本体（``agent.model.role_context.RoleContext``）；
- ``agent_instances``：agent 实例表，用于按 agent_id 定位实例；
- ``app_id_provider``：返回当前 app id 的可调用对象；
- ``default_agent_id_provider``：返回当前默认 agent id 的可调用对象；
- ``prompt_refresher``：系统提示词刷新回调——角色变了，提示词必须跟着变；
- ``app_state_saver``：app 状态落盘回调——角色变更需要持久化，否则重启丢失。

设计原则：**副作用失败不阻断主流程**。刷新提示词或落盘失败只记录 warning，
因为角色变更本身已经在内存中生效，用户仍可继续当前会话。
"""

from __future__ import annotations

import collections.abc
import logging
import typing

logger = logging.getLogger(__name__)

__all__ = ["RoleContextApplicationService"]


class RoleContextApplicationService:
    """角色上下文的读写与副作用编排。"""

    def __init__(
        self,
        *,
        role_context: typing.Any = None,
        agent_instances: typing.MutableMapping[str, typing.Any] | None = None,
        app_id_provider: typing.Callable[[], str | None] | None = None,
        default_agent_id_provider: typing.Callable[[], str | None] | None = None,
        prompt_refresher: typing.Callable[[], typing.Any] | None = None,
        app_state_saver: typing.Callable[[str], typing.Any] | None = None,
    ) -> None:
        self._role_context = role_context
        self._agent_instances = agent_instances if agent_instances is not None else {}
        self._app_id_provider = app_id_provider
        self._default_agent_id_provider = default_agent_id_provider
        self._prompt_refresher = prompt_refresher
        self._app_state_saver = app_state_saver

    # --- 只读属性（便于诊断与测试） ---

    @property
    def role_context(self) -> typing.Any:
        return self._role_context

    @property
    def agent_instances(self) -> typing.MutableMapping[str, typing.Any]:
        return self._agent_instances

    # --- 解析辅助 ---

    def _resolve_app_id(self) -> str | None:
        if self._app_id_provider is None:
            return None
        try:
            return self._app_id_provider()
        except Exception:  # noqa: BLE001 - 解析失败视为「无 app」，调用方降级
            logger.warning("app_id provider failed", exc_info=True)
            return None

    def default_agent_id(self) -> str | None:
        """返回当前默认 agent 标识；未注入 provider 时返回 ``None``。"""
        if self._default_agent_id_provider is None:
            return None
        try:
            return self._default_agent_id_provider()
        except Exception:  # noqa: BLE001
            logger.warning("default_agent_id provider failed", exc_info=True)
            return None

    # --- 副作用 ---

    def _refresh_prompt(self) -> None:
        """刷新系统提示词；失败不阻断。"""
        if self._prompt_refresher is None:
            return
        try:
            self._prompt_refresher()
        except Exception:  # noqa: BLE001 - 提示词刷新属增强，失败不阻断角色变更
            logger.warning("system prompt refresh failed after role change", exc_info=True)

    def _persist_app_state(self) -> None:
        """落盘 app 状态；无 app id 或未注入保存器时跳过。"""
        if self._app_state_saver is None:
            return
        app_id = self._resolve_app_id()
        if not app_id:
            logger.debug("skip persisting app state: app id is unavailable")
            return
        try:
            self._app_state_saver(app_id)
        except Exception:  # noqa: BLE001 - 落盘失败只告警，内存中的角色变更已生效
            logger.warning("failed to persist app state after role change", exc_info=True)

    # --- 对外操作 ---

    def set_role_context(self, agent_id: str | None, context: typing.Any) -> typing.Any:
        """设置某 agent 的角色上下文，并触发刷新与落盘。

        :param agent_id: 目标 agent；``None`` 时落到当前默认 agent。
        :param context: 角色上下文；存储层按 ``dict`` 保存，非映射入参会被包装为
            ``{"value": context}``，避免因类型不符抛 ``ValueError``。
        :return: 实际写入的值；无法写入（无存储/无 agent）时返回 ``None``。
        """
        target = agent_id or self.default_agent_id()
        if target is None:
            logger.warning("role context set skipped: no target agent id")
            return None
        if self._role_context is None:
            logger.warning("role context storage is unavailable, skip set")
            return None
        setter = getattr(self._role_context, "set", None)
        if not callable(setter):
            logger.warning("role context storage does not support set()")
            return None
        payload = _as_context_mapping(context)
        try:
            setter(str(target), payload)
        except Exception:  # noqa: BLE001 - 存储实现差异不应打断调用方
            logger.warning("failed to set role context for %s", target, exc_info=True)
            return None
        self._refresh_prompt()
        self._persist_app_state()
        return context

    def get_role_context(self, agent_id: str | None = None) -> typing.Any:
        """读取某 agent 的角色上下文；不存在返回 ``None``。"""
        target = agent_id or self.default_agent_id()
        if self._role_context is None or target is None:
            return None
        getter = getattr(self._role_context, "get", None)
        if not callable(getter):
            return None
        try:
            return getter(str(target))
        except Exception:  # noqa: BLE001
            logger.warning("failed to read role context for %s", target, exc_info=True)
            return None

    def clear_role_context(self, agent_id: str | None = None) -> bool:
        """清除某 agent 的角色上下文，并触发刷新与落盘。

        存储层的 ``remove`` 返回 ``None``（存在与否都静默），因此这里以
        「清除前是否存在」作为返回值依据。
        """
        target = agent_id or self.default_agent_id()
        if self._role_context is None or target is None:
            return False
        existed = self.get_role_context(str(target)) is not None
        remover = getattr(self._role_context, "remove", None)
        if not callable(remover):
            return False
        try:
            remover(str(target))
        except Exception:  # noqa: BLE001
            logger.warning("failed to clear role context for %s", target, exc_info=True)
            return False
        if existed:
            self._refresh_prompt()
            self._persist_app_state()
        return existed

    def agent_instance(self, agent_id: str) -> typing.Any:
        """按标识取 agent 实例；不存在返回 ``None``。"""
        return self._agent_instances.get(str(agent_id))

    def __repr__(self) -> str:
        return (
            "RoleContextApplicationService("
            f"has_role_context={self._role_context is not None}, "
            f"agents={len(self._agent_instances)})"
        )


def _as_context_mapping(context: typing.Any) -> dict[str, typing.Any]:
    """把任意角色上下文归一为 ``dict``。

    ``RoleContext.set`` 按 ``dict`` 保存，直接传入字符串等非映射值会在
    ``dict(context)`` 处抛 ``ValueError``。这里统一包装，让调用方可以用
    「字符串 / 对象 / 映射」任意形式表达角色，而不必关心存储层细节。
    """
    if context is None:
        return {}
    if isinstance(context, collections.abc.Mapping):
        return dict(context)
    return {"value": context}
