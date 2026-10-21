import threading
import typing
import uuid


@typing.runtime_checkable
class AgentIdGenerator(typing.Protocol):
    """Agent 标识生成器协议。

    调用方只需要「能生成一个新的、当前进程内不重复的字符串标识」这一条语义，
    因此这里保持最小契约。
    """

    def generate(self, prefix: str = "agent") -> str:
        """生成一个新的 Agent 标识。"""
        ...

    # 兼容按调用点习惯使用的命名。
    def new_id(self, prefix: str = "agent") -> str:
        """``generate`` 的别名。"""
        ...


class DefaultAgentIdGenerator:
    """默认的 Agent 标识生成器。

    基于 ``uuid4`` 生成短标识，进程内唯一；内部持有计数器，保证同一毫秒内
    批量生成时也保持单调递增的可读前缀。
    """

    __slots__ = ("_prefix", "_counter", "_lock")

    def __init__(self, *, prefix: str = "agent") -> None:
        self._prefix = str(prefix) if prefix else "agent"
        self._counter = 0
        self._lock = threading.Lock()

    def generate(self, prefix: str | None = None) -> str:
        """生成形如 ``<prefix>-<序号>-<随机串>`` 的标识。"""
        name = self._prefix if prefix is None else (str(prefix) or self._prefix)
        with self._lock:
            self._counter += 1
            sequence = self._counter
        return f"{name}-{sequence}-{uuid.uuid4().hex[:12]}"

    def new_id(self, prefix: str | None = None) -> str:
        """``generate`` 的别名。"""
        return self.generate(prefix)

    def __repr__(self) -> str:
        return f"DefaultAgentIdGenerator(prefix={self._prefix!r})"
