"""专家包的可移植格式。

**已知缺口**（迁移遗留，非本次重组引入）：
原 ``expert/portable_package.py`` 在迁移中被清空为 0 字节，
但 ``expert_source`` 仍按「可移植包解析」这一职责调用本模块的两个函数。
这里给出显式占位，让缺口在调用点直接暴露，而不是在更深的调用栈里
变成 ``AttributeError: module has no attribute``。

补齐实现时，把两个函数替换为真实解析逻辑即可，调用方无需改动。
"""

from pathlib import Path
from typing import Any

__all__ = ["load_resource_lock", "apply_mcp_secret_overrides"]


def load_resource_lock(directory: Path) -> Any:
    """读取专家包的资源锁。

    待补齐：原实现负责校验包内资源的一致性并返回锁对象。
    """
    raise NotImplementedError(
        "专家包资源锁尚未实现："
        "原 expert/portable_package.py 在迁移中被清空为 0 字节，"
        "load_resource_lock 的真实实现待补齐。"
    )


def apply_mcp_secret_overrides(
    configuration: Any,
    *,
    secret_store: Any = None,
    **overrides: Any,
) -> Any:
    """把 MCP 密钥覆盖项应用到专家配置。

    待补齐：原实现负责从密钥库读取并解密后覆盖配置中的敏感字段。
    """
    raise NotImplementedError(
        "MCP 密钥覆盖尚未实现："
        "原 expert/portable_package.py 在迁移中被清空为 0 字节，"
        "apply_mcp_secret_overrides 的真实实现待补齐。"
    )
