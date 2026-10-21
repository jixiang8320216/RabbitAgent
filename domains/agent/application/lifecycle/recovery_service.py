"""Agent 恢复服务。

进程重启后，之前存在的子 Agent 可能处于「已 spawn 但未完成」的中间状态。
恢复服务负责在新进程启动时把它们找回来，让会话可以续跑。

三个协作者（全部由 composition 层注入）：

- ``registry_loader``：加载「应用 -> Agent 清单」，回答「应该有哪些 Agent」；
- ``execution_store``：读取每个 Agent 的执行进度；
- ``manifest_repository``：读取 spawn 时的清单快照；
- ``message_queue``：恢复后把待处理信号重新投递。

设计原则：**恢复失败不阻断启动**。任何一步读取失败都只记录 warning 并跳过
该 Agent——恢复是增强能力，让用户能正常发起新会话优先于找回历史状态。
"""

from __future__ import annotations

import logging
import typing

logger = logging.getLogger(__name__)

__all__ = ["AgentRecoveryService", "RecoveryCandidate"]


class RecoveryCandidate(typing.NamedTuple):
    """一个待恢复的 Agent。

    :param app_id: 所属应用标识。
    :param agent_id: Agent 标识。
    :param progress: 执行记录中的进度信息，可能为空。
    :param manifest: spawn 清单，可能为空。
    """

    app_id: str
    agent_id: str
    progress: typing.Mapping[str, typing.Any] = {}
    manifest: typing.Mapping[str, typing.Any] = {}


class AgentRecoveryService:
    """扫描并恢复未完成的子 Agent。"""

    def __init__(
        self,
        *,
        registry_loader: typing.Callable[..., typing.Any] | None = None,
        execution_store: typing.Any = None,
        manifest_repository: typing.Any = None,
        message_queue: typing.Any = None,
        registery_loader: typing.Callable[..., typing.Any] | None = None,
    ) -> None:
        # ``registery_loader`` 是调用点里出现的 typo，两个拼写都接受。
        loader = registry_loader if registry_loader is not None else registery_loader
        self._registry_loader = loader
        self._execution_store = execution_store
        self._manifest_repository = manifest_repository
        self._message_queue = message_queue

    # --- 只读属性，便于诊断与测试 ---

    @property
    def execution_store(self) -> typing.Any:
        return self._execution_store

    @property
    def manifest_repository(self) -> typing.Any:
        return self._manifest_repository

    @property
    def message_queue(self) -> typing.Any:
        return self._message_queue

    @property
    def registry_loader(self) -> typing.Callable[..., typing.Any] | None:
        return self._registry_loader

    # --- 扫描 ---

    def collect_candidates(self, registry: typing.Any = None) -> list[RecoveryCandidate]:
        """收集待恢复的 Agent 列表。

        :param registry: 已加载的注册表；``None`` 时通过 ``registry_loader`` 加载。
        """
        if registry is None:
            registry = self._load_registry()
        candidates: list[RecoveryCandidate] = []
        for app_id, agents in _iter_registry_entries(registry):
            for entry in agents:
                agent_id = _extract_agent_id(entry)
                if not agent_id:
                    continue
                candidates.append(
                    RecoveryCandidate(
                        app_id=app_id,
                        agent_id=agent_id,
                        progress=self._load_progress(agent_id),
                        manifest=self._load_manifest(entry),
                    )
                )
        return candidates

    def _load_registry(self) -> typing.Any:
        """调用注入的 loader；异常时返回空结果。"""
        if self._registry_loader is None:
            return {}
        try:
            return self._registry_loader()
        except Exception:  # noqa: BLE001 - 恢复是增强能力，不阻断启动
            logger.warning("failed to load agent registry for recovery", exc_info=True)
            return {}

    def _load_progress(self, agent_id: str) -> typing.Mapping[str, typing.Any]:
        loader = getattr(self._execution_store, "load", None)
        if not callable(loader):
            return {}
        try:
            payload = loader(agent_id)
        except Exception:  # noqa: BLE001
            logger.warning("failed to load execution record for %s", agent_id, exc_info=True)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _load_manifest(self, entry: typing.Any) -> typing.Mapping[str, typing.Any]:
        spawn_id = ""
        if isinstance(entry, dict):
            spawn_id = str(entry.get("spawn_id") or "")
        else:
            spawn_id = str(getattr(entry, "spawn_id", "") or "")
        if not spawn_id:
            return {}
        loader = getattr(self._manifest_repository, "load", None)
        if not callable(loader):
            return {}
        try:
            payload = loader(spawn_id)
        except Exception:  # noqa: BLE001
            logger.warning("failed to load spawn manifest %s", spawn_id, exc_info=True)
            return {}
        return payload if isinstance(payload, dict) else {}

    # --- 恢复 ---

    async def recover(self, *, app_id: str | None = None) -> list[RecoveryCandidate]:
        """执行恢复流程，返回处理过的候选列表。

        当前阶段只做「识别 + 重新投递唤醒信号」，真正的 Agent 重建由上层
        orchestrator 完成——恢复服务不持有应用对象，避免循环依赖。
        """
        candidates = [
            item for item in self.collect_candidates() if app_id is None or item.app_id == str(app_id)
        ]
        for item in candidates:
            self._notify(item)
        if candidates:
            logger.info("[AGENT RECOVERY] %d candidate(s) discovered", len(candidates))
        return candidates

    def _notify(self, candidate: RecoveryCandidate) -> None:
        """把恢复发现投递到消息队列，供上层消费。"""
        putter = getattr(self._message_queue, "put", None)
        if not callable(putter):
            return
        try:
            putter(
                kind="agent_recovery_candidate",
                agent_id=candidate.agent_id,
                payload={"app_id": candidate.app_id},
            )
        except Exception:  # noqa: BLE001
            logger.warning("failed to enqueue recovery signal for %s", candidate.agent_id, exc_info=True)

    def __repr__(self) -> str:
        return f"AgentRecoveryService(has_loader={self._registry_loader is not None})"


def _iter_registry_entries(registry: typing.Any) -> typing.Iterator[tuple[str, typing.Iterable[typing.Any]]]:
    """把注册表归一为 ``(app_id, agents)`` 序列。

    兼容两种形状：``{app_id: [agent, ...]}``，或带 ``apps`` 键的包装对象。
    """
    if registry is None:
        return
    if isinstance(registry, dict):
        for app_id, agents in registry.items():
            yield str(app_id), (agents or [])
        return
    apps = getattr(registry, "apps", None)
    if isinstance(apps, dict):
        for app_id, agents in apps.items():
            yield str(app_id), (agents or [])


def _extract_agent_id(entry: typing.Any) -> str:
    """从注册表条目中取出 Agent 标识。"""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("agent_id") or entry.get("id") or entry.get("name") or "")
    return str(getattr(entry, "agent_id", "") or getattr(entry, "id", "") or "")
