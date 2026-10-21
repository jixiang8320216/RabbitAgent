import asyncio
import logging
from collections import deque
from typing import TypeVar, Generic, Optional, Callable, Awaitable

TurnT = TypeVar("TurnT")

logger = logging.getLogger(__name__)


class AgentTurnQueue(Generic[TurnT]):
    """
    Agent turn 队列：串行执行 agent turn，避免并发 turn 互相干扰。

    构造时接收一个 async 回调 ``executor``（如 ``TUIApplication._execute_queued_user_message``
    或 ``CLIApplication._execute_queued_agent_turn``），每个 turn 提交后由
    ``executor`` 处理；若有活跃 turn 正在执行，新 turn 入队等待，按 FIFO 串行执行。
    """

    def __init__(self, executor: Callable[[TurnT], Awaitable[None]]) -> None:
        self._executor = executor
        self._pending:deque[TurnT] = deque()
        self._worker_task: asyncio.Task[None] |None = None
        self._turn_active = False

    def submit(self, turn: TurnT) -> Optional[int]:
        """
        提交一个 turn。

        - 若当前无活跃 turn（或活跃 task 已结束）：立即调度执行，返回 ``None``。
        - 否则入队等待，返回排队位置（当前待处理条数，正整数）。

        返回值约定与 CLI 调用点 ``if pending_position: ...`` 一致：
        falsy 表示立即执行，正整数表示排队等待数。
        """
        was_busy= self.is_busy
        pending_position = len(self._pending)+(1 if self._turn_active else 0) if was_busy else 0
        self._pending.append(turn)
        if not was_busy:
            self._worker_task = asyncio.create_task(self._drain())
        return pending_position

    def discard_pending(self) -> None:
        """丢弃所有待处理 turn（不影响正在执行的活跃 turn）。"""
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        """当前排队等待的 turn 数（不含正在执行的）。"""
        return len(self._pending)

    @property
    def is_busy(self) -> bool:
        """是否有 turn 正在执行。"""
        return self._worker_task is not None and not self._worker_task.done()

    async def _drain(self) -> None:
        try:
            while self._pending:
                turn = self._pending.popleft()
                self._turn_active = True
                try:
                    await self._executor(turn)
                except asyncio.CancelledError :
                    raise
                except Exception as ex:
                    logger.error(ex)
                finally:
                   self._turn_active = False
        finally:
            self._worker_task = None

