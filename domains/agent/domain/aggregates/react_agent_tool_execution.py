"""工具批量执行器（ToolBatchExecutor）。

``ReactReplyOrchestrator`` 在构造期就会尝试装载它（``_load_tool_executor``），
装载不到就退回内联的签名探测路径——所以**这个类缺失不会让启动炸，只会让工具
调用少一层并发与聚合能力**。

调用方（``_run_executor``）认这些入口名：

``execute`` / ``execute_batch`` / ``execute_tools`` / ``run_batch`` / ``run`` /
``__call__``，参数形状为 ``(blocks,)`` 或 ``tool_uses=`` / ``blocks=`` /
``tool_calls=``。这里把它们全部实现为同一入口的别名。

两条硬约束：

1. **单个工具失败不能让整批陪葬**。某个工具抛异常，要变成一条带错误文本的
   ``ToolResultBlock`` 回灌给模型——否则模型会以为工具静默成功然后继续瞎猜。
2. **返回 ``None`` 与返回空列表是两回事**。空列表是「执行了但没结果」，
   ``None`` 是「压根没执行」。混同会让调用方的语义判断失效。

并发由 ``host.parrallel_tool_calls``（项目内既有拼写）控制；默认串行，
保证工具副作用顺序可预期。
"""

from __future__ import annotations

import asyncio
import collections.abc
import inspect
import logging
import typing

import domains.agent.domain.value_objects.message as message

logger = logging.getLogger("tool-execution")

__all__ = ["ToolBatchExecutor", "ToolExecutionError"]

_LOG_PREFIX = "[TOOL-EXEC] "


class ToolExecutionError(RuntimeError):
    """工具执行失败。"""


class ToolBatchExecutor:
    """一批 ``ToolUseBlock`` 的执行器。

    :param host: 宿主 agent（取其 ``toolkit`` 与并发开关）。
    :param app_resolver / session_id_resolver: 透传给工具上下文的解析器。
    """

    def __init__(
        self,
        host: typing.Any,
        *,
        app_resolver: typing.Any = None,
        session_id_resolver: typing.Any = None,
    ) -> None:
        self._host = host
        self._app_resolver = app_resolver
        self._session_id_resolver = session_id_resolver

    # --- 入口别名 ----------------------------------------------------------

    async def execute(
        self,
        tool_uses: list[message.ToolUseBlock] | None = None,
        *,
        blocks: list[message.ToolUseBlock] | None = None,
        tool_calls: list[message.ToolUseBlock] | None = None,
    ) -> list[message.ToolResultBlock]:
        """执行一批工具调用，返回与入参**等长同序**的结果块列表。"""
        batch = self._pick_batch(tool_uses, blocks, tool_calls)
        if not batch:
            return []
        # 两条路径都必须把异常收回成结果块：并发路径靠 ``return_exceptions``，
        # 串行路径靠 :meth:`_run_one` 自身不抛——否则串行时一个工具炸掉会让
        # 整批工具全部消失，模型拿到空观察继续瞎猜。
        if self._parallel_enabled() and len(batch) > 1:
            outcomes = await asyncio.gather(
                *(self._run_one(block) for block in batch),
                return_exceptions=True,
            )
        else:
            outcomes = []
            for block in batch:
                try:
                    outcomes.append(await self._run_one(block))
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001 - 转成结果块回灌模型
                    outcomes.append(exc)
        return [self._as_result(block, outcome) for block, outcome in zip(batch, outcomes)]

    execute_batch = execute
    execute_tools = execute

    async def run_batch(self, blocks=None, **kwargs) -> list[message.ToolResultBlock]:
        return await self.execute(blocks, **kwargs)

    async def run(self, blocks=None, **kwargs) -> list[message.ToolResultBlock]:
        return await self.execute(blocks, **kwargs)

    async def __call__(self, blocks=None, **kwargs) -> list[message.ToolResultBlock]:
        return await self.execute(blocks, **kwargs)

    # --- 单个工具 ----------------------------------------------------------

    async def _run_one(self, block: object) -> object:
        """执行一个工具；异常向上传播，由 :meth:`_as_result` 统一转成结果块。"""
        name = str(self._field(block, "name") or "")
        arguments = self._field(block, "input")
        if not isinstance(arguments, collections.abc.Mapping):
            arguments = {} if arguments is None else {"input": arguments}

        toolkit = getattr(self._host, "toolkit", None)
        if toolkit is None:
            raise ToolExecutionError(
                f"agent {self._host_name()!r} has no toolkit; cannot execute tool {name!r}"
            )

        for attr in (
            "async_execute_tool",
            "aexecute_tool",
            "execute_tool",
            "call_tool",
            "invoke_tool",
            "run_tool",
            "ainvoke_tool",
            "execute",
            "act",
            "call",
            "__call__",
        ):
            target = getattr(toolkit, attr, None)
            if not callable(target):
                continue
            for args, kwargs in (
                ((name, dict(arguments)), {}),
                ((), {"name": name, "arguments": dict(arguments)}),
                ((), {"tool_name": name, "arguments": dict(arguments)}),
                ((name,), dict(arguments)),
            ):
                try:
                    produced = target(*args, **kwargs)
                except TypeError as exc:
                    # 只吞「本帧」抛出的绑定失败；工具**内部**抛的 TypeError
                    # 必然带一层自己的栈帧，那种是真 bug，必须往外传，否则会被
                    # 误判成签名不匹配而静默跳过，错误永久消失。
                    if exc.__traceback__ is not None and exc.__traceback__.tb_next is not None:
                        raise
                    continue
                if inspect.isawaitable(produced):
                    produced = await produced
                return produced

        # 兜底：从 toolkit 取出工具对象本身再调用
        tool = self._resolve_tool(toolkit, name)
        if tool is None:
            raise ToolExecutionError(f"tool {name!r} not found on toolkit")
        if not callable(tool):
            raise ToolExecutionError(f"tool {name!r} is not callable")
        produced = tool(**dict(arguments))
        if inspect.isawaitable(produced):
            produced = await produced
        return produced

    @staticmethod
    def _resolve_tool(toolkit: object, name: str) -> object:
        for attr in ("get_tool", "get", "tool", "resolve"):
            getter = getattr(toolkit, attr, None)
            if not callable(getter):
                continue
            try:
                found = getter(name)
            except TypeError:
                continue
            except Exception:  # noqa: BLE001
                continue
            if found is not None:
                return found
        return getattr(toolkit, name, None)

    # --- 归一化 ------------------------------------------------------------

    def _as_result(self, block: object, outcome: object) -> message.ToolResultBlock:
        """把单个产出/异常包成结果块；**永不抛**。"""
        if isinstance(outcome, message.ToolResultBlock):
            return outcome
        if isinstance(outcome, BaseException):
            detail = f"{type(outcome).__name__}: {outcome}"
            logger.error("%stool %r failed: %s", _LOG_PREFIX, self._field(block, "name"), detail)
        elif outcome is None:
            detail = "tool produced no result"
        else:
            detail = self._stringify(outcome)
        return message.ToolResultBlock(
            type="tool_result",
            id=str(self._field(block, "id") or ""),
            name=str(self._field(block, "name") or ""),
            output=detail,
        )

    @staticmethod
    def _stringify(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, collections.abc.Mapping):
            for key in ("output", "content", "text", "result"):
                if key in value:
                    return str(value[key])
            return str(dict(value))
        if isinstance(value, (list, tuple)):
            return "\n".join(ToolBatchExecutor._stringify(item) for item in value)
        return str(value)

    @staticmethod
    def _field(block: object, key: str) -> object:
        if isinstance(block, collections.abc.Mapping):
            return block.get(key)
        return getattr(block, key, None)

    # --- 辅助 --------------------------------------------------------------

    @staticmethod
    def _pick_batch(*candidates: object) -> list[object]:
        for candidate in candidates:
            if isinstance(candidate, (list, tuple)) and candidate:
                return list(candidate)
        return []

    def _parallel_enabled(self) -> bool:
        return bool(getattr(self._host, "parallel_tool_calls", False))

    def _host_name(self) -> str:
        return str(getattr(self._host, "name", "") or "")

    def __repr__(self) -> str:  # pragma: no cover
        return f"ToolBatchExecutor(host={self._host_name()!r})"
