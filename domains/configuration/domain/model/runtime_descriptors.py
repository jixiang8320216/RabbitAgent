"""向外部客户端广告的**运行时描述符**（模式 / 模型）。

为什么这两个类型放在配置域而不放在 ``interfaces/acp``
----------------------------------------------------

``SessionMode`` 与 ``ModelInfo`` 原先定义在 ``interfaces/acp/schema.py``，
而真正在构造它们的是配置域的合并器
（``configuration.infrastructure.merge.config_merger``）—— 于是形成了
**域层反向依赖接口层**的格局。

按依赖方向，协议层可以认识业务模型，业务模型不能认识协议层。所以这里把定义
下沉到配置域，``interfaces/acp/schema.py`` 改为 re-export：

- 依赖方向恢复正确（``interfaces -> domains``）；
- 两侧的对象是**同一个类**，既有调用点的类型判断与 isinstance 全部不受影响；
- ACP 特有的别名（``modelId`` / ``_meta``）随类型一起搬过来，协议语义不丢。
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Optional

from pydantic import BaseModel, Field

__all__ = ["ModelInfo", "SessionMode"]


class ModelInfo(BaseModel):
    model_config = {
        "populate_by_name": True,
    }

    field_meta: Annotated[
        Optional[Dict[str, Any]],
        Field(
            alias="_meta",
            description="The meta information of the model",
        ),
    ] = None
    description: Annotated[Optional[str], Field(description="Optional description of the model.")] = None
    model_id: Annotated[str, Field(alias="modelId", description="Unique identifier of the model.")]
    name: Annotated[str, Field(description="Unique identifier of the model.")]


class SessionMode(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
