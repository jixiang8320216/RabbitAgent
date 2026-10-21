import enum

import pydantic


class ProviderRetryState(enum.StrEnum):
    WAITING = "waiting"
    RETRYING = "retrying"
    RECOVERING = "recovered"
    EXHAUSTING = "exhausted"
    INTERRUPTED = "interrupted"
    CANCELED = "canceled"

class ProviderRetryStatus(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)

    state:ProviderRetryState
    cause:ProviderRetryState