from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_agent_registry(registry_dir: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """从 APP V3 目录布局加载 agent 注册表。

    返回 ``{app_id: [agent 定义, ...]}``：
    - `registry_dir` 缺省时使用 RuntimeContext 的 apps_dir；
    - 每个子目录 ``app_{id}/agents/`` 下的 ``*.json`` 视为一份 agent 定义；
    - 目录不存在、JSON 损坏一律跳过，最终降级为空 dict，不抛异常
      （恢复流程是尽力而为的降级路径，不应阻断启动）。
    """
    base = Path(registry_dir) if registry_dir else _default_apps_dir()
    if base is None:
        return {}

    registry: dict[str, list[dict[str, Any]]] = {}
    if not base.is_dir():
        return registry

    for app_dir in sorted(base.iterdir()):
        if not app_dir.is_dir() or not app_dir.name.startswith("app_"):
            continue
        agents_dir = app_dir / "agents"
        if not agents_dir.is_dir():
            continue
        agents: list[dict[str, Any]] = []
        for agent_file in sorted(agents_dir.glob("*.json")):
            try:
                payload = json.loads(agent_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("invalid agent definition skipped: %s", agent_file)
                continue
            if isinstance(payload, dict):
                agents.append(payload)
        if agents:
            registry[app_dir.name] = agents
    return registry


def _default_apps_dir() -> Path | None:
    """从 RuntimeContext 取 apps_dir；上下文未就绪时返回 None。"""
    try:
        from domains.agent.domain.value_objects.runtime_context import get_runtime_context_or_none

        context = get_runtime_context_or_none()
    except Exception:
        return None
    if context is None:
        return None
    apps_dir = getattr(context, "apps_dir", None)
    return Path(apps_dir) if apps_dir else None
