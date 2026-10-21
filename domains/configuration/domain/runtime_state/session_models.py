"""会话模型的历史兼容外壳。

真正的模型已迁至同目录下的其他模块：
- ``ExecutionContext`` → :mod:`execution_context`
- ``DefaultRecord``  → 尚无定义，见下方说明

保留本模块仅为兼容既有 import 路径。
"""

from domains.configuration.domain.runtime_state.execution_context import (
    ExecutionContext,
    PlanExecutionContext,
    SubtaskExecutionState,
)

__all__ = [
    "ExecutionContext",
    "PlanExecutionContext",
    "SubtaskExecutionState",
]
