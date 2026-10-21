"""文件版 Agent 执行记录存储。

「执行记录」（execution store）保存每个子 Agent 一次 spawn 的执行状态，
供进程重启后的恢复流程（``AgentRecoveryService``）判断「哪些 Agent 曾存在、
执行到哪一步」。本模块的工厂函数 :func:`create_file_agent_execution_store`
按默认落盘位置创建一个文件实现。

落盘位置的选择顺序：

1. ``RuntimeContext.apps_dir``（APP v3 持久化根目录，每个应用一个文件夹）；
2. ``RuntimeContext.sessions_dir``（会话目录，兜底）；
3. 系统临时目录下的 ``rabbit-agent-executions``（最后兜底）。

之所以逐级降级而不是直接抛错：恢复能力属于「尽力而为」的增强功能，其存储
不可用不应阻断应用启动。每次降级都会记录 warning，操作员仍能感知。

文件名沿用模块名中的既定 typo（``fiel_`` 应为 ``file_``），模块名保持与
导入点一致；类名使用正确拼写。
"""

from __future__ import annotations

import json
import logging
import pathlib
import tempfile
import threading
import typing

from domains.agent.domain.entities import agent_context
import domains.agent.domain.entities.agent_execution_paths as agent_execution_paths
import domains.persistence.infrastructure.file as file_persistence
logger = logging.getLogger(__name__)

__all__ = ["FileAgentExecutionStore", "create_file_agent_execution_store"]


class FileAgentExecutionStore:
    """以「一个 Agent 一个 JSON 文件」的方式保存执行记录。

    目录结构::

        <base_dir>/<agent_id>/execution.json

    写操作为原子替换（先写临时文件再 ``replace``），避免进程中断留下半截
    JSON 导致后续读取失败。
    """

    _RECORD_NAME = "execution.json"

    def __init__(self, sessions_dir: str | pathlib.Path) -> None:
        self._sessions_dir = pathlib.Path(sessions_dir)
        self._lock = threading.RLock()


    def paths(self, context: agent_context.AgentContext) -> agent_execution_paths.AgentExecutionPaths:
        return agent_execution_paths.AgentExecutionPaths.for_context(self._sessions_dir, context)

    def save_state(self, context: agent_context.AgentContext) -> None:
        # ``AgentContext`` 是 pydantic 模型，没有自定义 ``to_dict()``；
        # 原代码调用 ``context.to_dict()`` 会抛
        # "'AgentContext' object has no attribute 'to_dict'"。改用 pydantic 原生
        # ``model_dump(mode="json")``——它同时保证 datetime 等非 JSON 原生类型被
        # 正确序列化，``registry`` 字段已在模型侧标记 ``exclude=True``。
        file_persistence.atomic_write_json(
            self.paths(context).state_path,
            context.model_dump(mode="json"),
        )




def _resolve_default_base_dir() -> pathlib.Path:
    """逐级选择一个可用的默认落盘目录。"""
    try:
        from domains.agent.domain.value_objects.runtime_context import get_runtime_context

        context = get_runtime_context()
    except Exception:  # noqa: BLE001 - 运行上下文未安装时直接走临时目录
        context = None

    if context is not None:
        for candidate in (getattr(context, "apps_dir", None), getattr(context, "sessions_dir", None)):
            if candidate:
                return pathlib.Path(candidate).expanduser().resolve(strict=False) / "agent_executions"

    fallback = pathlib.Path(tempfile.gettempdir()) / "rabbit-agent-executions"
    logger.warning("execution store falls back to temporary directory: %s", fallback)
    return fallback


def create_file_agent_execution_store(
    base_dir: str | pathlib.Path | None = None,
) -> FileAgentExecutionStore:
    """创建文件版执行记录存储。

    :param base_dir: 显式指定根目录；``None`` 时按 RuntimeContext 逐级降级。
    """
    target = pathlib.Path(base_dir) if base_dir is not None else _resolve_default_base_dir()
    return FileAgentExecutionStore(target)
