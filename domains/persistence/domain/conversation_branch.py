"""会话分支的检查点边界定义。

一次对话会被切成若干「分支检查点」（checkpoint），用于工作区版本管理与回滚。
检查点记录在两个时刻：**进入某个执行单元之前**与**结束之后**——只有同时有
「前/后」两侧，回滚时才能确定一个完整的操作区间。

枚举名沿用调用点 ``session_turn_execution.py`` 的拼写 ``CheckpointBoudary``
（boundary 少了 ``n``）；同时导出正确拼写 ``CheckpointBoundary`` 作为别名，
新代码优先用后者。
"""

from __future__ import annotations

import enum

__all__ = ["CheckpointBoudary", "CheckpointBoundary"]


class CheckpointBoudary(str, enum.Enum):
    """检查点边界。"""

    # --- 根 agent 轮次 ---
    BEFORE_ROOT_TURN = "before_root_turn"
    AFTER_ROOT_TURN = "after_root_turn"

    # --- 子 agent 轮次 ---
    BEFORE_SUBAGENT_TURN = "before_subagent_turn"
    AFTER_SUBAGENT_TURN = "after_subagent_turn"

    # --- 工具调用 ---
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"

    # --- 上下文压缩 / 溢出卸载 ---
    BEFORE_CONTEXT_COMPACTION = "before_context_compaction"
    AFTER_CONTEXT_COMPACTION = "after_context_compaction"


# 正确拼写的别名
CheckpointBoundary = CheckpointBoudary
