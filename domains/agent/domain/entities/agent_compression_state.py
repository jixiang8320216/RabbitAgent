"""单个 agent 的「历史压缩」运行状态。

此前本文件是 **0 字节空壳**，而调用方已经在按完整契约消费它：

- ``domains.agent.application.scheduling.scheduler.AgentScheduler`` 用
  ``is_compressing`` / ``compression_run_id`` 判断能否安全 spawn 子 agent
  （压缩会整体替换上下文，压缩期间塞子 agent 进去会读到半成品）；
- ``domains.agent.domain.entities.agent_scheduling.StartAgentInput`` 把本对象
  带进启动参数；``AgentCompressionBusyTimeout`` 在超时时打印 ``compression_run_id``。

也就是说：**这一项是链路上的一环，缺口在这里会让整个「压缩门」退化成摆设**。

生命周期约定（重要）
--------------------

``AgentScheduler._wait_for_compression_gate_unlocked`` 的循环是

.. code-block:: python

    while current is not None:
        await asyncio.sleep(...)
        current = self._get_compression_state(agent_id)

它只会在 ``get_agent_compression_state()`` 返回 ``None`` 时退出。因此：

- 压缩开始 -> ``begin()`` 并把状态对象挂上去；
- 压缩结束 -> 宿主**必须把它卸下来**（``HistoryCompressionRegistry.finish``
  会做这件事），否则 spawn 会被拦到 ``COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS``
  超时为止；
- 状态对象本身保留统计信息，可以在卸下后再被读出来做日志 / 遥测。
"""

from __future__ import annotations

import datetime
import typing
import uuid

__all__ = ["AgentCompressionState"]


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


class AgentCompressionState:
    """一个 agent 的压缩运行状态 + 最近一次压缩的统计。

    :ivar compression_run_id: 当前压缩批次标识；``None`` 表示当前没有在压缩。
    :ivar is_compressing: 是否正在压缩。与 ``compression_run_id`` 冗余一位，
        是为了同时满足「快速布尔判断」与「日志可追踪」两种消费方式。
    :ivar strategy: 最近一次使用的策略：``"model"``（模型摘要）/
        ``"extractive"``（规则式摘要）/ ``"none"``。
    """

    __slots__ = (
        "agent_id",
        "compression_run_id",
        "is_compressing",
        "strategy",
        "compression_rounds",
        "messages_before",
        "messages_after",
        "tokens_before",
        "tokens_after",
        "started_at",
        "finished_at",
        "last_error",
    )

    def __init__(
            self,
            agent_id: str,
            *,
            compression_run_id: str | None = None,
            is_compressing: bool = False,
            strategy: str = "none",
            compression_rounds: int = 0,
            messages_before: int = 0,
            messages_after: int = 0,
            tokens_before: int = 0,
            tokens_after: int = 0,
            started_at: str | None = None,
            finished_at: str | None = None,
            last_error: str = "",
    ) -> None:
        self.agent_id = agent_id
        self.compression_run_id = compression_run_id
        self.is_compressing = is_compressing
        self.strategy = strategy
        self.compression_rounds = compression_rounds
        self.messages_before = messages_before
        self.messages_after = messages_after
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.started_at = started_at
        self.finished_at = finished_at
        self.last_error = last_error

    # -- 生命周期 ---------------------------------------------------------

    def begin(self) -> str:
        """标记一次压缩开始。

        :return: 本次的 ``compression_run_id``。
        """
        self.compression_run_id = uuid.uuid4().hex
        self.is_compressing = True
        self.started_at = _now()
        self.last_error = ""
        return self.compression_run_id

    def finish(
            self,
            *,
            strategy: str,
            messages_before: int,
            messages_after: int,
            tokens_before: int,
            tokens_after: int,
    ) -> None:
        """标记一次压缩完成，并累加统计。

        注意：``is_compressing`` 置 ``False``、``compression_run_id`` 置 ``None``
        表示「这批压缩结束了」；**把它从宿主的注册表里摘掉**是调用方的责任。
        """
        self.is_compressing = False
        self.compression_run_id = None
        self.strategy = strategy or "none"
        self.messages_before = messages_before
        self.messages_after = messages_after
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.compression_rounds += 1
        self.finished_at = _now()
        self.last_error = ""

    def fail(self, error: str) -> None:
        """标记失败。**必须**同时清掉「正在压缩」标记，否则 spawn 门会挂死。"""
        self.is_compressing = False
        self.compression_run_id = None
        self.last_error = str(error or "")
        self.finished_at = _now()

    # -- 读视图 -----------------------------------------------------------

    @property
    def reduced_tokens(self) -> int:
        """最近一次压缩省下的 token 数；未完成压缩时为 ``0``。"""
        if not self.compression_rounds:
            return 0
        return max(0, self.tokens_before - self.tokens_after)

    def snapshot(self) -> dict[str, typing.Any]:
        """导出可序列化的快照（日志 / 会话状态持久化用）。"""
        return {
            "agent_id": self.agent_id,
            "compression_run_id": self.compression_run_id,
            "is_compressing": self.is_compressing,
            "strategy": self.strategy,
            "compression_rounds": self.compression_rounds,
            "messages_before": self.messages_before,
            "messages_after": self.messages_after,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "reduced_tokens": self.reduced_tokens,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_error": self.last_error,
        }

    def __repr__(self) -> str:
        return (
            f"AgentCompressionState(agent_id={self.agent_id!r}, "
            f"run_id={self.compression_run_id!r}, rounds={self.compression_rounds}, "
            f"strategy={self.strategy!r})"
        )
