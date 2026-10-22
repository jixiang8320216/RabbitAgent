"""ACP 协议层的 schema 定义。

``ModelInfo`` / ``SessionMode`` 的定义本体已经下沉到配置域
（:mod:`domains.configuration.domain.model.runtime_descriptors`），这里只做
re-export。这样做的原因与收益见那边的模块文档，核心是**把依赖方向摆正**：
协议层可以认识业务模型，业务模型不该认识协议层。

两处写法都不变：本模块对外的名字一个都没改，既有
``from interfaces.acp.schema import SessionMode`` 的写法继续可用，
并且拿到的**是同一个类**。
"""

from domains.configuration.domain.model.runtime_descriptors import (
    ModelInfo,
    SessionMode,
)

__all__ = ["ModelInfo", "SessionMode"]
