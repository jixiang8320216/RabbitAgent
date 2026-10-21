
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict

LifecycleDispatchStatus = Literal["fired","deferred","skipped"]

class _LifecycleProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

class LifecycleDispatchResult(_LifecycleProjection):
    status: LifecycleDispatchStatus
    trigger_type: str = Field(min_length=1)
    seseion_id: str|None
    reason:str|None=None
    run_id: str|None
    counter_snapshots: dict[str,int] = Field(default_factory=dict)

class LifecycleOutcomeStatus(_LifecycleProjection):
    session_id: str|None=None
