import typing
import datetime as datetime_module
import pydantic

NoEmptyString=typing.Annotated[
    pydantic.StrictStr,
    pydantic.StringConstraints(strip_whitespace=True, min_length=1),
]

def utc_now()->datetime_module.datetime:
    return datetime_module.datetime.now(datetime_module.UTC)

class EventPayload(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    timestamp: pydantic.AwareDatetime = pydantic.Field(default_factory=utc_now)

    def to_wire_data(self) -> dict[str, typing.Any]:
        """序列化成可直接落盘/传输的 JSON 兼容字典。

        ``EventProtocol.build_*`` 与 ``tui_observer`` 都会调用本方法，但基类一直
        没有实现它，导致事件持久化路径上抛
        ``'AgentTextPayload' object has no attribute 'to_wire_data'``。

        用 ``mode="json"`` 而非默认的 python 模式：``timestamp`` 是 AwareDatetime，
        python 模式会留下 datetime 对象无法 JSON 序列化，json 模式则转成 ISO 字符串。
        """
        return self.model_dump(mode="json")
class AgentTextPayload(EventPayload):
    agent_name: str
    content: pydantic.StrictStr
    is_streaming:pydantic.StrictBool=True
    operation_id:NoEmptyString|None=None
    instance_id: NoEmptyString|None=None
