"""把记忆能力挂进 agent 的工具集。

记忆对 agent 暴露为**一个工具**（``memory``），而不是一组散装接口：
agent 用统一的 ``action`` 参数表达「记一下 / 查一下 / 忘一下」，
工具内部再路由到具体的记忆类型与存储层。

**为什么是单工具**：记忆操作种类多（增删改查、合并、遗忘、统计），
拆成多个工具会挤占模型的工具预算，也会让模型在「该用哪个」上纠结。
一个工具 + ``action`` 参数更省 token，也更好约束。

**历史缺口**：本模块此前是 0 字节文件，而
``runtime_composition._attach_memory_tools`` 会调用
``memory_agent_tools.register_memory_tools(toolkit, runtime)`` ——
记忆能力装配链路整体不可用（虽然 ``MemoryTool`` 本身实现完整）。
"""

from __future__ import annotations

import logging
import typing

logger = logging.getLogger(__name__)

#: 记忆工具在工具集里的名字
MEMORY_TOOL_NAME = "memory"


def register_memory_tools(
    toolkit: typing.Any,
    runtime: typing.Any,
) -> typing.Any | None:
    """把记忆工具注册进工具集。

    :param toolkit: 目标工具集
    :param runtime: 记忆运行时（``MemoryRuntimeService``），提供存储路径与设置
    :return: 注册成功的工具对象；未注册时返回 ``None``

    注册失败只记日志 —— 记忆是**可选能力**，不该让 agent 装配失败。
    """
    if toolkit is None or runtime is None:
        return None

    try:
        from domains.memory.base import MemoryConfig
        from domains.tool.builtin.memory_tool import MemoryTool
    except Exception:  # noqa: BLE001 - 依赖缺失时降级为「无记忆」
        logger.debug("memory tools unavailable: dependency import failed", exc_info=True)
        return None

    try:
        settings = getattr(runtime, "settings", None)
        storage_path = _resolve_storage_path(settings)

        tool = MemoryTool(
            user_id=_resolve_user_id(settings),
            memory_config=MemoryConfig(storage_path=storage_path) if storage_path else None,
        )
    except Exception:  # noqa: BLE001
        logger.debug("memory tools unavailable: tool construction failed", exc_info=True)
        return None

    try:
        register = getattr(toolkit, "register_tool_function", None)
        if register is None:
            logger.debug("memory tools unavailable: toolkit has no register_tool_function")
            return None
        register(
            tool.execute,
            name=MEMORY_TOOL_NAME,
            description=(
                "Memory tool - store and retrieve conversation history, "
                "knowledge and experience. Actions: add / search / update / "
                "remove / summary / stats / forget / consolidate / clear_all."
            ),
            group="memory",
            source="builtin",
        )
    except Exception:  # noqa: BLE001
        logger.debug("memory tool registration failed", exc_info=True)
        return None

    logger.info("memory tool registered")
    return tool


def _resolve_storage_path(settings: typing.Any) -> str:
    """从运行时设置里解析存储根目录。"""
    if settings is None:
        return ""
    for attr in ("user_memory_file", "project_memory_file"):
        path = getattr(settings, attr, None)
        if path is None:
            continue
        try:
            return str(pathlib_path(path).parent)
        except Exception:  # noqa: BLE001
            continue
    return ""


def _resolve_user_id(settings: typing.Any) -> str:
    """解析记忆归属的用户标识。"""
    if settings is None:
        return "default_user"
    value = getattr(settings, "user_id", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "default_user"


def pathlib_path(value: typing.Any) -> typing.Any:
    """把路径值转成 ``pathlib.Path``。"""
    import pathlib

    return pathlib.Path(value)


__all__ = ["MEMORY_TOOL_NAME", "register_memory_tools"]
