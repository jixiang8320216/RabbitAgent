"""自动压缩的抑制记录。

``AgentInstance.auto_compact_suppression`` 的类型：某些时段（比如正在跑子
agent、正在做工具批处理）不希望上下文自动压缩介入，就挂一条抑制记录，
压缩调度看到它存在就跳过这一轮。

目前在仓库里只有 ``AgentInstance`` 引用本类，字段按「一条抑制记录该有的最小
信息」定义：谁发起的、为什么、什么时候生效、什么时候失效。
"""

import datetime as datetime_module
import typing

import pydantic


class AgentAutoCompactSuppression(pydantic.BaseModel):
    """一条「本轮不要自动压缩」的标记。"""

    model_config = pydantic.ConfigDict(extra="forbid", validate_assignment=True, strict=True)

    reason: str | None = None
    run_id: str | None = None
    created_at: str = pydantic.Field(
        default_factory=lambda: datetime_module.datetime.now(tz=datetime_module.UTC).isoformat()
    )
    expires_at: str | None = None

    def is_expired(self, now: typing.Optional[datetime_module.datetime] = None) -> bool:
        """是否已过期。``expires_at`` 为空表示不过期。"""
        if self.expires_at is None:
            return False
        current = now or datetime_module.datetime.now(tz=datetime_module.UTC)
        try:
            deadline = datetime_module.datetime.fromisoformat(self.expires_at)
        except ValueError:
            # 时间串坏了就当成未过期：宁可少压缩一次，也不要误判成可压缩。
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=datetime_module.UTC)
        return current >= deadline
