"""取消作用域。

一次用户消息（turn）在执行期间需要一个可取消、可观测生命周期的句柄，
这就是 :class:`TurnCancelScope`：

- **可取消**：用户中断（ESC / Ctrl+C）时调用 :meth:`TurnCancelScope.cancel`，
  正在等待的代码可以通过 :meth:`TurnCancelScope.wait_cancelled` 或
  :meth:`TurnCancelScope.raise_if_cancelled` 感知；
- **可观测**：``terminated`` 为真表示本轮已结束（正常完成 / 取消 / 失败），
  ``SessionTurnExecutor`` 用它决定要不要再打一次边界标记；
- **可嵌套**：子 turn 挂在父 scope 下，父级被取消时子级联动取消；父级已终止
  时子级立即处于取消态（避免「父轮已死、子轮还在跑」）。

状态机是**一次性**的：第一个终态转换生效，后续 ``complete`` / ``cancel`` /
``failed`` 都是空操作（返回 ``False``），这样调用方可以在 ``finally`` 里无条件
收尾而不必担心覆盖真实结果。

并发说明：本对象可能同时被事件循环线程（执行 turn）与中断发起方访问，因此
状态转换用可重入锁保护；``asyncio.Event`` 用于跨协程唤醒。
"""

from __future__ import annotations

import asyncio
import enum
import threading
import typing
import uuid

__all__ = [
    "TurnCancelState",
    "TurnCancelScope",
    "BackgroundExecutionScope",
    "new_turn_id",
]


class TurnCancelState(str, enum.Enum):
    """轮次作用域的生命周期状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


_TERMINAL_STATES = frozenset(
    {TurnCancelState.COMPLETED, TurnCancelState.CANCELLED, TurnCancelState.FAILED}
)


def new_turn_id() -> str:
    """生成一个新的轮次标识。"""
    return str(uuid.uuid4())


class TurnCancelScope:
    """单次 turn 的取消作用域。

    :param parent: 父作用域（通常是 :meth:`InterruptManager.get_session_scope`
        返回的会话级作用域）；父级终止会联动本作用域。
    :param message_id: 触发本轮的用户消息标识，用于日志与诊断定位。
    :param turn_id: 轮次标识；通常在 :meth:`set_turn_id` 中后补——轮次标识由
        宿主的 ``_prepare_turn_identity`` 决定，构造时往往还拿不到。
    :param session_id: 所属会话标识；省略时从父作用域继承。
    """

    __slots__ = (
        "_session_id",
        "_turn_id",
        "_message_id",
        "_parent",
        "_state",
        "_reason",
        "_lock",
        "_cancel_event",
        "_children",
    )

    def __init__(
        self,
        parent: "TurnCancelScope | None" = None,
        *,
        message_id: str | None = None,
        turn_id: str | None = None,
        session_id: typing.Any = None,
    ) -> None:
        if session_id is None and parent is not None:
            session_id = getattr(parent, "session_id", None)
        self._session_id = str(session_id) if session_id is not None else None
        # 未显式给出 turn_id 时自动生成一个：轮次标识是 Trace / Token / 中断
        # 归因到具体轮次的唯一依据，恒为 None 会让长程一致性无法验证。
        # 宿主仍可在拿到真实 turn_id 后用 set_turn_id 覆盖。
        self._turn_id = turn_id if turn_id is not None else new_turn_id()
        self._message_id = message_id
        self._parent = parent
        self._state = TurnCancelState.RUNNING
        self._reason = ""
        self._lock = threading.RLock()
        self._cancel_event = asyncio.Event()
        self._children: list[TurnCancelScope] = []

        if parent is not None:
            with parent._lock:
                parent._children.append(self)
            # 父级已结束：本轮不该继续跑，直接落到取消态
            if parent.terminated:
                self._transition(
                    TurnCancelState.CANCELLED,
                    reason=parent.reason or "parent_terminated",
                )

    # --- 属性 ------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def turn_id(self) -> str | None:
        return self._turn_id

    @property
    def message_id(self) -> str | None:
        """触发本轮的用户消息标识。"""
        return self._message_id

    @property
    def state(self) -> TurnCancelState:
        return self._state

    @property
    def reason(self) -> str:
        """终态的原因说明；未终止时为空字符串。"""
        return self._reason

    @property
    def terminated(self) -> bool:
        """是否已进入终态（完成 / 取消 / 失败）。"""
        return self._state in _TERMINAL_STATES

    @property
    def cancelled(self) -> bool:
        return self._state is TurnCancelState.CANCELLED

    @property
    def completed(self) -> bool:
        return self._state is TurnCancelState.COMPLETED

    @property
    def failed_state(self) -> bool:
        """是否以失败告终（属性名避开 :meth:`failed`）。"""
        return self._state is TurnCancelState.FAILED

    @property
    def parent(self) -> "TurnCancelScope | None":
        return self._parent

    # --- 标识 ------------------------------------------------------------

    def set_turn_id(self, turn_id: str | None) -> None:
        """后补轮次标识（宿主准备好 turn_id 后调用）。"""
        self._turn_id = turn_id

    # --- 终态转换 --------------------------------------------------------

    def complete(self) -> bool:
        """标记本轮正常完成。"""
        return self._transition(TurnCancelState.COMPLETED)

    def cancel(self, reason: str = "") -> bool:
        """取消本轮，并联动取消所有子作用域。"""
        transitioned = self._transition(
            TurnCancelState.CANCELLED, reason=reason or "cancelled"
        )
        if transitioned:
            for child in tuple(self._children):
                child.cancel(reason=reason or "parent_cancelled")
        return transitioned

    def failed(self, reason: str = "") -> bool:
        """标记本轮失败结束。"""
        return self._transition(TurnCancelState.FAILED, reason=reason or "failed")

    def fail(self, reason: str = "") -> bool:
        """``failed`` 的别名（``_register_turn_scope`` 用此名）。"""
        return self.failed(reason=reason)

    # --- 取消感知 --------------------------------------------------------

    async def wait_cancelled(self) -> None:
        """等待本作用域被取消；已处于终态时立即返回。"""
        if self.terminated:
            return
        await self._cancel_event.wait()

    def raise_if_cancelled(self) -> None:
        """已取消则抛 ``asyncio.CancelledError``，供协作式取消点使用。"""
        if self.cancelled:
            raise asyncio.CancelledError(self._reason or "turn cancelled")

    # --- 内部 ------------------------------------------------------------

    def _transition(self, state: TurnCancelState, *, reason: str = "") -> bool:
        """执行一次终态转换；只有首个转换生效。"""
        with self._lock:
            if self.terminated:
                return False
            self._state = state
            self._reason = reason
        # 任何终态都唤醒等待方，避免协程永远挂起
        self._cancel_event.set()
        return True

    def __repr__(self) -> str:
        return (
            f"TurnCancelScope(session_id={self._session_id!r}, "
            f"turn_id={self._turn_id!r}, state={self._state.value!r})"
        )


class BackgroundExecutionScope(TurnCancelScope):
    """后台 agent 的执行作用域。

    与 :class:`TurnCancelScope` 的区别在于**生命周期**：后台 agent（spawn 出去的
    子任务）不属于某一个 turn —— 它在主 turn 结束后仍可能继续跑。因此它的
    父作用域是**会话级**作用域而非 turn 级，会话结束才随之终止。

    为什么需要独立类型：
        取消通道要能区分「取消这一轮对话」与「取消某个后台任务」。若复用
        turn 作用域，用户结束当前对话会把仍在工作的后台任务一起杀掉；
        若不区分，后台任务又无法被单独取消。

    :param parent: 会话级作用域。
    :param spawn_id: 后台 agent 的标识，用于按 id 精确寻址。
    """

    __slots__ = ("_spawn_id",)

    def __init__(
        self,
        parent: "TurnCancelScope | None" = None,
        *,
        spawn_id: str | None = None,
        session_id: typing.Any = None,
    ) -> None:
        super().__init__(parent, session_id=session_id)
        self._spawn_id = spawn_id

    @property
    def spawn_id(self) -> str | None:
        """后台 agent 的标识。"""
        return self._spawn_id

    def __repr__(self) -> str:
        return (
            f"BackgroundExecutionScope(session_id={self.session_id!r}, "
            f"spawn_id={self._spawn_id!r}, state={self.state.value!r})"
        )
