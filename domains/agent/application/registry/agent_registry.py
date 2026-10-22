"""Agent 注册表（内存实现）。

职责：登记会话内的 agent 上下文，并对外提供**状态广播**所需的查询能力 ——
调度器要靠它找出某个 agent 的子节点、查当前状态、切换等待模式。

为什么放在 application 层：
    它是「装配期可用的具体实现」，而接口形状由领域层的
    ``AgentRegistryPort``（``domains.agent.domain.entities.agent_context``）定义。
    领域层不依赖实现，实现也不反向污染领域契约。
    同目录的 ``model_registry.py``（``ModelRegistry``）遵循同一约定。

身份语义（重要）：
    注册表保存的是**调用方传入的那个上下文对象本身**，不做拷贝。
    调度器、ReActAgent 等持有者拿到 ``registry.get()`` 的结果后继续就地改写
    状态字段（``ctx.status = ...``），注册表必须能立即看到同一份状态；
    反过来 ``update_status`` 也必须改的是同一个对象，否则「谁改谁看得到」
    的约定就断了。

线程安全：
    注册表会被 hook 回调、调度器、状态广播多方读写。用 ``RLock`` 保护临界区，
    保证「读-改-写」的复合操作（如 ``update_status``）不会丢更新。
"""

from __future__ import annotations

import collections.abc
import threading
import typing

import domains.agent.domain.entities.agent_context as agent_context

__all__ = ["AgentRegistry"]


class AgentRegistry:
    """内存中的 agent 注册表。

    实现了 :class:`agent_context.AgentRegistryPort` 的全部方法，
    因此可以直接注入 ``AgentScheduler``。

    :param on_status: 可选的状态观察者。每次状态变更后以变更后的上下文调用一次；
        观察者抛出的异常一律吞掉——遥测失败不应该打断一轮对话。
    """

    def __init__(
        self,
        on_status: typing.Callable[[agent_context.AgentContext], None] | None = None,
    ) -> None:
        self._records: dict[str, agent_context.AgentContext] = {}
        self._children: dict[str, list[str]] = {}
        self._on_status = on_status
        self._lock = threading.RLock()

    # ── 登记 ────────────────────────────────────────────────────
    def register(self, context: agent_context.AgentContext) -> None:
        """登记（或覆盖）一个 agent 上下文。"""
        agent_id = getattr(context, "agent_id", None)
        if not agent_id:
            return
        with self._lock:
            self._records[str(agent_id)] = context
            parent_id = getattr(context, "parent_agent_id", None)
            if parent_id:
                siblings = self._children.setdefault(str(parent_id), [])
                if str(agent_id) not in siblings:
                    siblings.append(str(agent_id))

    # ── 查询 ────────────────────────────────────────────────────
    def get(self, agent_id: str) -> agent_context.AgentContext | None:
        """按 id 取上下文；不存在返回 None。"""
        with self._lock:
            return self._records.get(str(agent_id))

    def get_children(
        self, parent_id: str
    ) -> collections.abc.Sequence[agent_context.AgentContext]:
        """取某 agent 的直接子节点上下文。"""
        with self._lock:
            ids = list(self._children.get(str(parent_id), ()))
            return [
                record
                for record in (self._records.get(i) for i in ids)
                if record is not None
            ]

    # ── 状态变更 ────────────────────────────────────────────────
    def update_status(
        self,
        agent_id: object,
        status: agent_context.AgentStatus,
        current_tool: str | None = None,
    ) -> None:
        """更新 agent 状态（就地改写已登记的上下文对象）。

        :param current_tool: 为 ``None`` 表示「不改动当前工具」；显式传入字符串
            才覆盖。调用点（调度器、ReActAgent）普遍只关心状态，工具名由
            ``AgentContext.current_tool`` 就地赋值维护。
        """
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None:
                return
            record.status = status
            if current_tool is not None:
                record.current_tool = current_tool
        self._notify_status(record)

    def update_wait_mode(
        self, agent_id: str, wait_mode: agent_context.AgentWaitMode
    ) -> bool:
        """切换 agent 的等待模式。

        :return: 切换成功返回 True；agent 不存在返回 False。
        """
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None:
                return False
            record.wait_mode = wait_mode
            return True

    # ── 内部 ────────────────────────────────────────────────────
    def _notify_status(self, record: agent_context.AgentContext) -> None:
        """把状态变更同步给可选观察者；观察者异常一律吞掉。"""
        if self._on_status is None:
            return
        try:
            self._on_status(record)
        except Exception:  # noqa: BLE001 - 上报失败不应打断对话
            pass
