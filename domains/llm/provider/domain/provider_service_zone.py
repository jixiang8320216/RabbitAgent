"""Provider 服务档位（Service Zone）。

同一 provider 常提供多档服务（绿色通道 / 常规通道），差异体现在
**可用的模型范围**与**优先级**，而不是价格。

- ``GREEN`` —— 绿色通道：全量模型、优先调度，用于生产主链路
- ``YELLOW`` —— 黄色通道：受限模型集，用于降级与批量任务

**为什么要独立成概念**：它不是「模型名的一部分」，也不是「温度」这类
生成参数，而是**服务端侧的调度策略**。放进 provider 的 profile 里，
让同一次会话的所有调用共享同一档位。

**历史缺口**：本模块此前从未存在（迁移时丢失），而
``chat_model_creation`` 引用了 ``ProviderServiceZone.GREEN``，
导致 provider 域的模型创建路径完全不可用。
"""

from __future__ import annotations

import enum


class ProviderServiceZone(enum.StrEnum):
    """Provider 服务档位。"""

    #: 绿色通道 —— 全量模型、优先调度
    GREEN = "green"
    #: 黄色通道 —— 受限模型集，用于降级
    YELLOW = "yellow"

    @property
    def is_degraded(self) -> bool:
        """是否为降级档位。"""
        return self is not ProviderServiceZone.GREEN

    @classmethod
    def resolve(cls, value: "ProviderServiceZone | str | None") -> "ProviderServiceZone":
        """把任意输入解析成档位。

        无法识别时退回 ``GREEN`` —— 默认走全量通道，比意外降级安全。
        """
        if value is None:
            return cls.GREEN
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        if not normalized:
            return cls.GREEN
        try:
            return cls(normalized)
        except ValueError:
            return cls.GREEN


__all__ = ["ProviderServiceZone"]
