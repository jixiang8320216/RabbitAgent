"""Hook 执行运行时：把「快照里的 hook 定义」真正跑起来并产出裁决。

背景（修复记录）
----------------
本文件此前是**空文件**，导致验收项 L1-3.2（拦截有效性，P0）、
L1-3.4（失败策略，P0）、L1-3.3（误杀率）、L1-3.5（时序正确）
全部只能判 BLOCKED —— 因为「hook 自身崩溃时是 fail-close 还是
fail-open」这条判据**必须真的执行一次 hook 才能观察**，而项目当时
没有任何执行入口。域模型（HookSnapshot / HookPolicy / HookEvent /
HookDecisionAction / HookRunStatus）都已就位，唯独缺执行器。

设计要点
--------
1. **无 IO 依赖**：执行器只接收一个可调用对象（hook handler），不自己
   起子进程。单测可直接注入 lambda，避免在验收环境里真跑外部脚本。
2. **裁决聚合**：一个事件可挂多个 hook。裁决按「最严格优先」聚合——
   DENY/BLOCK > FAIL/CANCEL > ASK > DEFER/DECLINE > ALLOW/ACCEPT >
   CONTINUE。任何一条 DENY 即整体 DENY，符合安全语义。
3. **fail-close / fail-open 由 Policy + 事件敏感度共同决定**：这是
   L1-3.4 的核心。安全敏感事件（PreToolUse / PermissionRequest）
   默认 fail-close；``policy.continue_on_error=False`` 时一律
   fail-close。
4. **超时可控**：单条hook 超时记为 HookRunStatus.TIMEOUT，处理方式同样
   遵循策略，不让 hook 把主流程挂死（对应 L5-2.1 关注点）。
5. **时序可观测**：每次执行产出 :class:`HookExecutionTrace`，含每条
   hook 的耗时，供 L1-3.5「时序正确」判定，也便于排障。
"""

from __future__ import annotations

import logging
import threading
import time
import typing
from dataclasses import dataclass, field

from domains.hook.domain.hook_definitions import HookEvent
from domains.hook.domain.outputs import HookDecisionAction, HookRunStatus
from domains.hook.domain.resource_models import HookPolicy, HookSnapshot

logger = logging.getLogger(__name__)

__all__ = [
    "HookDefinition",
    "HookInvocation",
    "HookOutcome",
    "HookExecutionTrace",
    "HookRuntime",
    "SECURITY_CRITICAL_EVENTS",
    "DECISION_SEVERITY",
]

# 需要 fail-close 的安全敏感事件：这些事件上 hook 崩溃不能放行，
# 否则等于绕过安全检查。
SECURITY_CRITICAL_EVENTS: frozenset = frozenset({
    HookEvent.PRE_TOOL_USE,
    HookEvent.PERMISSION_REQUEST,
})

# 裁决严格度：数字越大越严格。用于多 hook 裁决聚合。
DECISION_SEVERITY: dict[HookDecisionAction, int] = {
    HookDecisionAction.CONTINUE: 0,
    HookDecisionAction.ALLOW: 1,
    HookDecisionAction.ACCEPT: 1,
    HookDecisionAction.DECLINE: 2,
    HookDecisionAction.DEFER: 2,
    HookDecisionAction.ASK: 3,
    HookDecisionAction.FAIL: 4,
    HookDecisionAction.CANCEL: 4,
    HookDecisionAction.DENY: 5,
    HookDecisionAction.BLOCK: 5,
}


def _as_decision(value: typing.Any) -> HookDecisionAction:
    """把 hook 返回的任意形态归一为 :class:`HookDecisionAction`。

    容忍三种写法：枚举本身、枚举字符串、以及 dict 形态
    ``{"decision": "deny", "reason": "..."}`` —— 后者是外部 hook
    脚本最常见的返回形式。
    """
    if isinstance(value, HookDecisionAction):
        return value
    if isinstance(value, dict):
        raw = value.get("decision") or value.get("action") or "continue"
    else:
        raw = value
    text = getattr(raw, "value", raw)
    try:
        return HookDecisionAction(str(text).strip().lower())
    except ValueError:
        logger.warning("无法识别的 hook 裁决 %r，按 continue 处理", raw)
        return HookDecisionAction.CONTINUE


def _as_reason(value: typing.Any) -> str:
    """提取裁决理由，缺失时给可读默认，避免调用方拿到空串。"""
    if isinstance(value, dict):
        return str(value.get("reason") or value.get("message") or "")
    return str(value or "")


@dataclass(frozen=True)
class HookDefinition:
    """单条 hook 定义。

    :param name:    hook 标识，用于 trace 与报错定位
    :param handler: 可调用对象，入参 :class:`HookInvocation`，返回裁决
                    （枚举 / 字符串 / dict 皆可）
    :param timeout: 单条超时秒数；None 表示用 policy 默认值
    :param matcher: 可选匹配函数，入参 HookInvocation 返回 bool；
                    False 时该 hook 被跳过（HookRunStatus.SKIPPED）
    """

    name: str
    handler: typing.Callable[["HookInvocation"], typing.Any]
    timeout: float | None = None
    matcher: typing.Callable[["HookInvocation"], bool] | None = None


@dataclass(frozen=True)
class HookInvocation:
    """一次 hook 调用的输入。"""

    event: typing.Any
    tool_name: str = ""
    tool_input: typing.Mapping[str, typing.Any] = field(default_factory=dict)
    session_id: str = ""
    payload: typing.Mapping[str, typing.Any] = field(default_factory=dict)

    @property
    def event_name(self) -> str:
        return str(getattr(self.event, "value", self.event))

    @property
    def is_security_critical(self) -> bool:
        return self.event in SECURITY_CRITICAL_EVENTS


@dataclass(frozen=True)
class HookOutcome:
    """单条 hook 的执行结果。"""

    name: str
    status: HookRunStatus
    decision: HookDecisionAction
    reason: str = ""
    elapsed_ms: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class HookExecutionTrace:
    """一次事件触发的完整执行轨迹。"""

    event: str
    outcomes: tuple[HookOutcome, ...]
    decision: HookDecisionAction
    reason: str
    elapsed_ms: float
    fail_closed: bool

    @property
    def denied(self) -> bool:
        return self.decision in (HookDecisionAction.DENY,
                                 HookDecisionAction.BLOCK)

    @property
    def needs_confirmation(self) -> bool:
        return self.decision is HookDecisionAction.ASK

    @property
    def statuses(self) -> tuple[str, ...]:
        return tuple(o.status.value for o in self.outcomes)

    def names(self) -> tuple[str, ...]:
        return tuple(o.name for o in self.outcomes)


class HookRuntime:
    """Hook 执行器。

    :param snapshot: 生效的 hook 快照
    :param policy:   执行策略（超时、失败处理）
    """

    __slots__ = ("_snapshot", "_policy")

    def __init__(
        self,
        snapshot: HookSnapshot | None = None,
        policy: HookPolicy | None = None,
        /,
        *,
        hook_snapshot: HookSnapshot | None = None,
        hook_policy: HookPolicy | None = None,
    ) -> None:
        self._snapshot = (snapshot or hook_snapshot or HookSnapshot.empty())
        self._policy = policy or hook_policy or HookPolicy()

    @property
    def snapshot(self) -> HookSnapshot:
        return self._snapshot

    @property
    def policy(self) -> HookPolicy:
        return self._policy

    def update_snapshot(self, snapshot: HookSnapshot) -> None:
        self._snapshot = (snapshot if snapshot is not None
                          else HookSnapshot.empty())

    # ---- 单条执行 ----
    def _run_one(
        self,
        definition: typing.Any,
        invocation: HookInvocation,
        fail_close: bool,
    ) -> HookOutcome:
        """执行单条 hook，把任何异常 / 超时都收敛成 HookOutcome。

        这里**绝不向上抛异常**：hook 是增强能力，其故障不能让主流程
        以未分类错误崩掉。是否放行由 fail_close 决定。
        """
        name = getattr(definition, "name", None) or type(definition).__name__
        timeout = getattr(definition, "timeout", None)
        if timeout is None:
            timeout = self._policy.default_timeout_seconds

        # matcher 不匹配 -> 跳过（非错误）
        matcher = getattr(definition, "matcher", None)
        if matcher is not None:
            try:
                if not matcher(invocation):
                    return HookOutcome(
                        name, HookRunStatus.SKIPPED,
                        HookDecisionAction.CONTINUE, "matcher 未命中",
                    )
            except Exception as error:  # noqa: BLE001
                logger.warning("hook %s matcher 异常: %s", name, error)
                detail = f"{type(error).__name__}: {error}"
                if fail_close:
                    return HookOutcome(
                        name, HookRunStatus.FAILED,
                        HookDecisionAction.DENY,
                        "matcher 异常，按 fail-close 阻断", error=detail)
                return HookOutcome(
                    name, HookRunStatus.FAILED,
                    HookDecisionAction.CONTINUE,
                    "matcher 异常，按 fail-open 放行", error=detail)

        handler = getattr(definition, "handler", None)
        if handler is None or not callable(handler):
            msg = "hook 定义缺少可调用 handler"
            if fail_close:
                return HookOutcome(name, HookRunStatus.FAILED,
                                   HookDecisionAction.DENY, msg, error=msg)
            return HookOutcome(name, HookRunStatus.FAILED,
                               HookDecisionAction.CONTINUE, msg, error=msg)

        started = time.perf_counter()

        def _target(result_holder: dict,
                    error_holder: dict) -> None:
            try:
                result_holder["v"] = handler(invocation)
            except BaseException as error:  # noqa: BLE001
                error_holder["e"] = error

        result_holder: dict[str, typing.Any] = {}
        error_holder: dict[str, BaseException] = {}
        thread = threading.Thread(target=_target,
                                  args=(result_holder, error_holder),
                                  daemon=True)
        thread.start()
        thread.join(timeout)
        elapsed = (time.perf_counter() - started) * 1000

        if thread.is_alive():
            msg = f"hook 执行超时（>{timeout}s）"
            logger.warning("hook %s %s", name, msg)
            if fail_close:
                return HookOutcome(
                    name, HookRunStatus.TIMEOUT, HookDecisionAction.DENY,
                    msg, elapsed_ms=elapsed, error=msg)
            return HookOutcome(
                name, HookRunStatus.TIMEOUT, HookDecisionAction.CONTINUE,
                msg, elapsed_ms=elapsed, error=msg)

        if "e" in error_holder:
            error = error_holder["e"]
            detail = f"{type(error).__name__}: {error}"
            logger.warning("hook %s 崩溃: %s", name, detail)
            if fail_close:
                return HookOutcome(
                    name, HookRunStatus.FAILED, HookDecisionAction.DENY,
                    "hook 崩溃，按 fail-close 阻断",
                    elapsed_ms=elapsed, error=detail)
            return HookOutcome(
                name, HookRunStatus.FAILED, HookDecisionAction.CONTINUE,
                "hook 崩溃，按 fail-open 放行",
                elapsed_ms=elapsed, error=detail)

        raw = result_holder.get("v")
        return HookOutcome(
            name, HookRunStatus.COMPLETED,
            _as_decision(raw), _as_reason(raw), elapsed_ms=elapsed,
        )

    # ---- 事件级执行 ----
    def dispatch(self, invocation: HookInvocation) -> HookExecutionTrace:
        """按事件取出全部 hook 并按顺序执行，聚合出最终裁决。

        fail-close 判定（后者更安全，覆盖前者）：

        1. 事件属:data:`SECURITY_CRITICAL_EVENTS` -> fail-close
        2. :class:`HookPolicy.continue_on_error` 为 False -> fail-close
        """
        started = time.perf_counter()
        definitions = self._snapshot.for_event(invocation.event_name)
        fail_close = (invocation.is_security_critical
                      or not self._policy.continue_on_error)

        if not definitions:
            return HookExecutionTrace(
                invocation.event_name, (), HookDecisionAction.CONTINUE,
                "无 hook 配置", 0.0, fail_close,
            )

        outcomes = tuple(self._run_one(d, invocation, fail_close)
                         for d in definitions)

        # 最严格裁决胜出
        final = max((o.decision for o in outcomes),
                    key=lambda d: DECISION_SEVERITY.get(d, 0))
        reasons = [f"{o.name}: {o.reason}" for o in outcomes
                   if o.reason and o.decision is final]
        elapsed = (time.perf_counter() - started) * 1000
        return HookExecutionTrace(
            invocation.event_name, outcomes, final,
            "；".join(reasons) or "按裁决强度聚合", elapsed, fail_close,
        )

    def run_event(self, event: typing.Any,
                  **kwargs: typing.Any) -> HookExecutionTrace:
        """便捷入口：以事件名构造 :class:`HookInvocation` 并执行。"""
        return self.dispatch(HookInvocation(event=event, **kwargs))

    def has_hooks(self, event: typing.Any = None) -> bool:
        if event is None:
            return not self._snapshot.is_empty
        return bool(self._snapshot.for_event(
            str(getattr(event, "value", event))))

    def __repr__(self) -> str:
        return (f"HookRuntime(events={len(self._snapshot.event_names())}, "
                f"policy.continue_on_error={self._policy.continue_on_error})")