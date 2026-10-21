import datetime as datetime_module
import typing

import pydantic


class AgentCallState(pydantic.BaseModel):
    """一次 agent 调用的现场状态，随调用全程流转（开始 → 结束）。

    由 ``agent.service.agent_call_lifecycle_hooks`` 在 ``pre_reply`` 阶段创建、
    存进 ``agent_call_context``，在 ``post_reply`` 阶段取出用于遥测与 observer
    通知。因此这些字段不是装饰：``start_time`` 用来算耗时，``instance_id`` /
    ``parent_id`` 用来串调用链，``is_root_invocation`` 决定要不要记根调用。

    字段全部给了默认值或允许为空——调用现场拿不到某一项时，宁可留空也不该让
    一次调用因为状态建模而失败。
    """

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    instance_id: str = ""
    parent_id: str = ""
    is_root_invocation: bool = False
    user_message: str = ""
    start_time: datetime_module.datetime = pydantic.Field(
        default_factory=lambda: datetime_module.datetime.now(datetime_module.UTC)
    )
    metadata: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
