"""会话快照的历史兼容外壳。

真正的模型已迁至 :mod:`snapshot`。本模块保留仅为兼容既有 import 路径。
"""

from domains.configuration.domain.runtime_state.snapshot import (
    RuntimeConfigSnapshot,
)

__all__ = ["RuntimeConfigSnapshot"]
