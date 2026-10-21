
import enum
import pydantic

_STANDARD_BLOCKED_IMPORTS=frozenset(
    {
        "ctypes",
        "http",
        "marshal",
        "multiprocessing",
        "pickle",
        "shelve",
        "socketserver",
        "subprocess",
        "xml",
    }
)

class ExecutionPolicy(enum.StrEnum):
    TRUSTED="trusted"
    STANDARD="standard"
    STRICT="strict"

class ExecutionGuardConfig(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        extra = "forbid",
        frozen=True,
    )
    policy:ExecutionPolicy= ExecutionPolicy.STANDARD
    timeout_seconds: float = pydantic.Field(default=10.0, gt=0.0, le=300.0)
    allowed_imports: frozenset[str] = frozenset()
    blocked_imports:frozenset[str] =_STANDARD_BLOCKED_IMPORTS
    max_output_length:int=pydantic.Field(default=50_000, ge=1)


class PluginExecutionGuard:

    def __init__(self, config:ExecutionGuardConfig|None = None) -> None:
        self._config = config or ExecutionGuardConfig()
