"""8 种应用模式的**差异化编排**。

背景（也是为什么要补这个模块）
------------------------------

``builtin_mode_declarations.py`` 补齐了 8 种模式的 ``ModeDeclaration`` 之后，
「模式名 -> 槽位拓扑 -> 提示词」这条链路才成立。但**声明本身不等于行为**：

- 槽位只是「有哪些角色」，没有说这些角色**怎么跑**；
- 此前唯一能体现差异的开关是 ``parallel_tool_calls``（只影响单次 LLM 请求里
  工具是否并发），**没有独立的顺序编排器或并发编排器**；
- 结果就是 ``pipeline`` / ``parallel`` / ``debate`` / ``guarded`` 四种模式在运行时
  表现趋同：都靠根 agent 的模型自己决定调谁，框架层没落任何强制约束。

本模块补的就是「第三个要素」——**执行语义**：

===========  ==================  ==========================================
模式          执行语义             实际行为
===========  ==================  ==========================================
solo          SINGLE              单槽位单轮，无委派可见性
delegate      DELEGATE            单根 + 动态委派（根仍主导，可调用子 agent）
roleplay      ROLEPLAY            单 root 槽位，但保留委派入口（角色是提示词层）
groupchat     SHARED_BOARD        多对等 agent 共享会话板
guarded       SUPERVISED          规划 ->（审批）-> 执行，两段串行且不允许回环
pipeline      SERIAL              多阶段串行，上一阶段产物作为下一阶段输入
parallel      FANOUT              根扇出到全部 worker，并发执行后按分数汇总
debate        ROUND_ROBIN         正反方交替 N 轮，再由裁判槽位汇总裁决
===========  ==================  ==========================================

为什么做成「可执行」而不是只做描述：描述性的计划表很容易变成又一张没人读的
文档（本工程已经出现过「声明支持但无定义」的静默降级）。这里每种语义都有对应的
真实执行分支，可以用假执行器在单测里跑出**可观测的差异**（顺序 / 并发 / 轮次）。

除了运行时执行，本模块还负责一件装配期的事：**按模式精化槽位可见性**
（:func:`refine_allowed_agents`）。槽位自己声明的 ``subagent_visibility`` 是「能不能看到」，
计划层再加一层「应不应该看到」——流水线不该让第三阶段的 worker 直接回头调第一阶段，
辩论的正反方不该互相委派。``mode_agent_creation`` 在构造每个 agent 时会调用它。
"""

from __future__ import annotations

import asyncio
import enum
import logging
import typing

import domains.plugin.domain as plugin_contracts

logger = logging.getLogger(__name__)

__all__ = [
    "ExecutionSemantics",
    "Aggregation",
    "StageSpec",
    "ModeExecutionPlan",
    "StageTrace",
    "ModeExecutionResult",
    "ModeOrchestrationService",
    "MODE_EXECUTION_PLANS",
    "resolve_execution_plan",
    "describe_execution_plan",
    "refine_allowed_agents",
]

_LOG_PREFIX = "[MODE-ORCH] "

#: 单槽位 / 未知模式的兜底语义。
_FALLBACK_SEMANTICS = "delegate"


class ExecutionSemantics(enum.StrEnum):
    """模式的执行语义。每种语义在 :class:`ModeOrchestrationService` 里有独立分支。"""

    #: 单槽位直接执行（``solo``）。
    SINGLE = "single"
    #: 单根主导 + 动态委派（``delegate`` / ``roleplay`` 的运行时形态）。
    DELEGATE = "delegate"
    #: 对等 agent 共享会话板（``groupchat``）。
    SHARED_BOARD = "shared_board"
    #: 规划 -> 审批 -> 执行，两段串行且禁止反向委派（``guarded``）。
    SUPERVISED = "supervised"
    #: 多阶段串行，前一阶段产物作为下一阶段输入（``pipeline``）。
    SERIAL = "serial"
    #: 根扇出到多个 worker 并发执行（``parallel``）。
    FANOUT = "fanout"
    #: 正反方交替多轮后由裁判汇总（``debate``）。
    ROUND_ROBIN = "round_robin"


class Aggregation(enum.StrEnum):
    """多 worker 结果的聚合方式。"""

    IDENTITY = "identity"          # 只有一个结果，原样返回
    LAST = "last"                  # 取最后一个阶段的输出
    CONCAT = "concat"              # 顺序拼接
    RANKED = "ranked"              # 按分数排序拼接
    JUDGE = "judge"                # 交给裁判槽位汇总


@typing.runtime_checkable
class StageInvoker(typing.Protocol):
    """阶段执行器：输入一段任务文本，返回一段结果文本。"""

    async def __call__(self, slot_name: str, task: str) -> str: ...


@typing.runtime_checkable
class ApprovalGate(typing.Protocol):
    """``guarded`` 模式下「计划是否放行」的判定钩子。

    原型 ``async (plan_text) -> bool``；返回 ``False`` 时执行槽位不会启动。
    """

    async def __call__(self, plan_text: str) -> bool: ...


class StageSpec(typing.NamedTuple):
    """执行计划中的一个阶段。

    :param slot_name: 本阶段由哪个槽位承担。
    :param role: 阶段角色名，用于日志与错误定位。
    :param description: 该阶段做什么（会出现在 :func:`describe_execution_plan` 输出里）。
    """

    slot_name: str
    role: str
    description: str = ""


class StageTrace(typing.NamedTuple):
    """一次阶段执行的留痕。"""

    slot_name: str
    role: str
    ok: bool
    elapsed_ms: int
    output_chars: int
    error: str = ""


class ModeExecutionResult(typing.NamedTuple):
    """编排执行结果。

    :param output: 最终输出文本。
    :param traces: 各阶段执行留痕（失败阶段也留痕，便于定位）。
    :param semantics: 实际使用的执行语义。
    """

    output: str
    traces: tuple[StageTrace, ...]
    semantics: ExecutionSemantics
    ok: bool


class ModeExecutionPlan(typing.NamedTuple):
    """一个模式的执行计划。

    :param mode: 模式名。
    :param semantics: 执行语义。
    :param root_slot: 根槽位名（发起 / 汇总的那一方）。
    :param worker_slots: worker 槽位名，顺序敏感（``SERIAL`` 依赖它）。
    :param aggregation: 多 worker 输出的聚合方式。
    :param stages: 展开后的阶段列表；空表示「无多阶段」（语义由 orchestrator 直接处理）。
    :param max_parallelism: ``FANOUT`` 的并发上限；``0`` 表示不限。
    :param rounds: ``ROUND_ROBIN`` 的辩论轮次。
    :param chained_delegation: worker 是否只能向下游 worker 委派（流水线链式约束）。
    :param forbid_peer_delegation: worker 之间是否禁止互相委派（并行 / 辩论）。
    :param notes: 备注，出现在 :func:`describe_execution_plan` 输出里。
    """

    mode: str
    semantics: ExecutionSemantics
    root_slot: str
    worker_slots: tuple[str, ...] = ()
    aggregation: Aggregation = Aggregation.IDENTITY
    stages: tuple[StageSpec, ...] = ()
    max_parallelism: int = 0
    rounds: int = 1
    chained_delegation: bool = False
    forbid_peer_delegation: bool = False
    notes: str = ""


def _plan_delegate() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="delegate",
        semantics=ExecutionSemantics.DELEGATE,
        root_slot="通用模式",
        notes="单根主导，按 Skill / MCP 启用状态动态决定可委派对象。",
    )


def _plan_solo() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="solo",
        semantics=ExecutionSemantics.SINGLE,
        root_slot="单 Agent",
        notes="不装配任何委派入口；子 agent 不可见，工具集也最窄。",
    )


def _plan_roleplay() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="roleplay",
        semantics=ExecutionSemantics.DELEGATE,
        root_slot="角色扮演模式",
        notes="拓扑与 delegate 相同，差异在角色上下文注入（由 role_context 承担）。",
    )


def _plan_groupchat() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="groupchat",
        semantics=ExecutionSemantics.SHARED_BOARD,
        root_slot="群聊模式",
        aggregation=Aggregation.CONCAT,
        notes="多对等 agent 共享会话板（context_strategy=share_board），由编排者轮转发言。",
    )


def _plan_guarded() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="guarded",
        semantics=ExecutionSemantics.SUPERVISED,
        root_slot="守护规划",
        worker_slots=("守护执行",),
        aggregation=Aggregation.LAST,
        stages=(
            StageSpec("守护规划", "planner", "产出计划，等待审批放行"),
            StageSpec("守护执行", "executor", "按批准后的计划落地"),
        ),
        chained_delegation=True,
        forbid_peer_delegation=True,
        notes="执行槽位不可反向委派回规划槽位，避免自己批准自己。",
    )


def _plan_pipeline() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="pipeline",
        semantics=ExecutionSemantics.SERIAL,
        root_slot="流水线调度",
        worker_slots=("流水线执行",),
        aggregation=Aggregation.LAST,
        stages=(
            StageSpec("流水线调度", "dispatcher", "把任务拆成有序阶段"),
            StageSpec("流水线执行", "worker", "按上一阶段产物继续推进"),
        ),
        chained_delegation=True,
        notes="串行：下一阶段的输入 = 上一阶段的输出，阶段顺序由本表固定而非模型临时决定。",
    )


def _plan_parallel() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="parallel",
        semantics=ExecutionSemantics.FANOUT,
        root_slot="并行调度",
        worker_slots=("并行执行",),
        aggregation=Aggregation.RANKED,
        max_parallelism=0,
        forbid_peer_delegation=True,
        notes="根扇出到全部 worker 并发执行（asyncio.gather），worker 之间互不可见，结果由根汇总。",
    )


def _plan_debate() -> ModeExecutionPlan:
    return ModeExecutionPlan(
        mode="debate",
        semantics=ExecutionSemantics.ROUND_ROBIN,
        root_slot="辩论裁判",
        worker_slots=("正方", "反方"),
        aggregation=Aggregation.JUDGE,
        rounds=2,
        forbid_peer_delegation=True,
        notes="正反方交替 N 轮（后一轮看到前一方的论证），最后由裁判槽位汇总裁决。",
    )


#: 8 种内置模式的执行计划。**顺序敏感**（``SERIAL`` 依赖 worker 顺序）。
MODE_EXECUTION_PLANS: dict[str, ModeExecutionPlan] = {
    "delegate": _plan_delegate(),
    "solo": _plan_solo(),
    "roleplay": _plan_roleplay(),
    "groupchat": _plan_groupchat(),
    "guarded": _plan_guarded(),
    "pipeline": _plan_pipeline(),
    "parallel": _plan_parallel(),
    "debate": _plan_debate(),
}


def _derive_from_declaration(
        mode: str,
        declaration: plugin_contracts.ModeDeclaration | None,
) -> ModeExecutionPlan:
    """为**未在表中登记**的模式（插件 / skill 自定义模式）推导计划。

    推导只看两件事——槽位数量与是否并发：

    - 0/1 个槽位 -> ``SINGLE``（没有可编排的对象）；
    - 根槽位 + 恰好 1 个 worker 且该 worker 不开并发 -> ``SUPERVISED``（规划-执行两段）；
    - 根槽位 + 若干 worker 且 worker 并发 -> ``FANOUT``；
    - 其余 -> ``DELEGATE``。

    这样自定义模式**不会**因为没有登记就退化成「什么都不做」。
    """
    if declaration is None:
        return MODE_EXECUTION_PLANS[_FALLBACK_SEMANTICS]

    root_name = str(getattr(declaration, "root_agent", "") or "")
    slots = dict(getattr(declaration, "agent_slots", None) or {})
    workers = tuple(name for name in slots if name != root_name)
    if not root_name or not workers:
        return ModeExecutionPlan(
            mode=str(declaration.name or mode),
            semantics=ExecutionSemantics.SINGLE,
            root_slot=root_name or mode,
            notes="单槽位模式，无多阶段编排。",
        )

    concurrent = all(bool(slots[w].parallel_tool_calls) for w in workers)
    if len(workers) == 1 and not concurrent:
        return ModeExecutionPlan(
            mode=str(declaration.name or mode),
            semantics=ExecutionSemantics.SUPERVISED,
            root_slot=root_name,
            worker_slots=workers,
            aggregation=Aggregation.LAST,
            stages=(
                StageSpec(root_name, "planner", "派生自自定义模式：根槽位负责规划"),
                StageSpec(workers[0], "executor", "派生自自定义模式：worker 负责执行"),
            ),
            chained_delegation=True,
            notes="由槽位拓扑推导得到（根 + 1 个串行 worker）。",
        )
    if len(workers) > 1 and concurrent:
        return ModeExecutionPlan(
            mode=str(declaration.name or mode),
            semantics=ExecutionSemantics.FANOUT,
            root_slot=root_name,
            worker_slots=workers,
            aggregation=Aggregation.RANKED,
            forbid_peer_delegation=True,
            notes="由槽位拓扑推导得到（根 + 多个并发 worker）。",
        )
    return ModeExecutionPlan(
        mode=str(declaration.name or mode),
        semantics=ExecutionSemantics.DELEGATE,
        root_slot=root_name,
        worker_slots=workers,
        notes="由槽位拓扑推导得到，按委派语义运行。",
    )


def resolve_execution_plan(
        mode: str | None,
        declaration: plugin_contracts.ModeDeclaration | None = None,
) -> ModeExecutionPlan:
    """取指定模式的执行计划。

    :param mode: 模式名；未知或为空时按 ``delegate`` 处理。
    :param declaration: 可选的模式声明；当模式未在内置表中登记时按它的拓扑推导。
    """
    normalized = str(mode or "").strip().lower()
    plan = MODE_EXECUTION_PLANS.get(normalized)
    if plan is not None:
        return plan
    if declaration is not None:
        return _derive_from_declaration(normalized, declaration)
    logger.debug("%sno execution plan for mode %r, falling back to %r", _LOG_PREFIX, mode, _FALLBACK_SEMANTICS)
    return MODE_EXECUTION_PLANS[_FALLBACK_SEMANTICS]


def describe_execution_plan(
        mode: str | None,
        declaration: plugin_contracts.ModeDeclaration | None = None,
) -> str:
    """把计划渲染成给人看的文本（CLI / TUI 展示用）。"""
    plan = resolve_execution_plan(mode, declaration)
    lines = [
        f"模式: {plan.mode}",
        f"执行语义: {plan.semantics}",
        f"根槽位: {plan.root_slot or '(未声明)'}",
        f"Worker 槽位: {', '.join(plan.worker_slots) if plan.worker_slots else '(无)'}",
        f"聚合方式: {plan.aggregation}",
    ]
    if plan.stages:
        lines.append("阶段:")
        for index, stage in enumerate(plan.stages, start=1):
            lines.append(f"  {index}. [{stage.role}] {stage.slot_name} — {stage.description}")
    else:
        lines.append("阶段: 单阶段（由根槽位一轮完成）")
    lines.append(f"链式委派: {'是' if plan.chained_delegation else '否'}")
    lines.append(f"禁止 worker 互调: {'是' if plan.forbid_peer_delegation else '否'}")
    if plan.semantics == ExecutionSemantics.ROUND_ROBIN:
        lines.append(f"辩论轮次: {plan.rounds}")
    if plan.semantics == ExecutionSemantics.FANOUT:
        lines.append(f"并发上限: {'不限' if plan.max_parallelism <= 0 else plan.max_parallelism}")
    if plan.notes:
        lines.append(f"说明: {plan.notes}")
    return "\n".join(lines)


def refine_allowed_agents(
        mode: str | None,
        declaration: plugin_contracts.ModeDeclaration | None,
        slot_name: str,
        base_names: typing.Sequence[str],
) -> list[str]:
    """按执行计划精化某个槽位的「可委派对象」名单。

    槽位自己声明的 ``subagent_visibility`` 回答的是「**能不能**看到」，
    这一层回答的是「**应不应该**看到」：

    - ``chained_delegation``（流水线 / 守护）：worker 只能向下游委派，
      并且**不允许反向**回到上游，否则阶段约束形同虚设；
    - ``forbid_peer_delegation``（并行 / 辩论）：worker 之间互相不可见，
      避免并行任务互相污染、辩论双方互相「参考」而失去对抗性。

    精化只做**收窄**，绝不放宽：名单里没有的名字不会被加回来。计划缺失或
    槽位不在计划中时原样返回 ``base_names``——精化是约束，不是数据来源。

    :param mode: 模式名。
    :param declaration: 模式声明，用于取计划里的槽位顺序。
    :param slot_name: 当前要计算的槽位。
    :param base_names: ``ModeAgentCreationService`` 已有逻辑算出的可见名单。
    :return: 精化后的名单（保序去重）。
    """
    plan = resolve_execution_plan(mode, declaration)
    if not base_names:
        return list(base_names)

    allowed = list(base_names)

    if plan.forbid_peer_delegation and plan.worker_slots:
        peers = set(plan.worker_slots)
        if slot_name in peers:
            allowed = [name for name in allowed if name not in peers]

    if plan.chained_delegation and slot_name in plan.worker_slots:
        # worker 之后没有下游 -> 它就是终点，不该再向外委派业务性 worker；
        # 但保留工具型子 agent（*utility 解析出来的名字不在 worker_slots 里）。
        index = plan.worker_slots.index(slot_name)
        downstream = set(plan.worker_slots[index + 1:])
        upstream = set(plan.worker_slots[:index])
        allowed = [name for name in allowed if name not in downstream and name not in upstream]

    if plan.root_slot and slot_name in plan.worker_slots and plan.aggregation != Aggregation.IDENTITY:
        # 汇总型模式下 worker 完成任务即可，不允许 worker 再把活派回 root。
        allowed = [name for name in allowed if name != plan.root_slot]

    return list(dict.fromkeys(allowed))


class ModeOrchestrationService:
    """按执行语义实际跑一次多阶段任务。

    调用方提供 ``invoke``（把「槽位名 + 任务文本」变成结果文本），本服务负责：

    - ``SINGLE`` / ``DELEGATE`` / ``SHARED_BOARD``：一次调用，语义差异体现在
      ``plan`` 上（这三个语义的并发与可见性约束在装配期已经生效）；
    - ``SERIAL``：按顺序执行，把上一阶段输出拼进下一阶段输入；
    - ``FANOUT``：``asyncio.gather`` 并发调用全部 worker，按聚合方式合并；
    - ``ROUND_ROBIN``：正反方交替 ``rounds`` 轮，每轮看到上一轮对方的论证，
      最后由裁判槽位汇总；
    - ``SUPERVISED``：先跑规划槽位，过审批钩子后再跑执行槽位（计划文本作为输入）。

    任一阶段失败都**只在自己的 trace 上留痕**，其余阶段继续；最终 ``ok`` 反映
    「是否所有阶段都成功」。这里刻意不做「一处失败整链崩溃」——应用层降级比
    抛栈更符合本工程约定。
    """

    __slots__ = ()

    async def execute(
            self,
            *,
            plan: ModeExecutionPlan,
            task: str,
            invoke: StageInvoker,
            approve: ApprovalGate | None = None,
    ) -> ModeExecutionResult:
        """按计划执行一次任务。"""
        import time

        started = time.perf_counter()
        traces: list[StageTrace] = []

        try:
            if plan.semantics == ExecutionSemantics.SERIAL:
                output = await self._run_serial(plan, task, invoke, traces)
            elif plan.semantics == ExecutionSemantics.FANOUT:
                output = await self._run_fanout(plan, task, invoke, traces)
            elif plan.semantics == ExecutionSemantics.ROUND_ROBIN:
                output = await self._run_round_robin(plan, task, invoke, traces)
            elif plan.semantics == ExecutionSemantics.SUPERVISED:
                output = await self._run_supervised(plan, task, invoke, traces, approve)
            else:
                output = await self._run_identity(plan, task, invoke, traces)
        except Exception as exc:  # 编排层自身异常不影响调用方拿到部分结果
            logger.warning("%sorchestration failed for mode %r: %s", _LOG_PREFIX, plan.mode, exc, exc_info=True)
            return ModeExecutionResult(
                output="",
                traces=tuple(traces),
                semantics=plan.semantics,
                ok=False,
            )

        logger.debug(
            "%smode=%s semantics=%s stages=%d elapsed_ms=%.1f",
            _LOG_PREFIX, plan.mode, plan.semantics, len(traces), (time.perf_counter() - started) * 1000,
        )
        return ModeExecutionResult(
            output=output,
            traces=tuple(traces),
            semantics=plan.semantics,
            ok=all(trace.ok for trace in traces),
        )

    # -- 各语义的实际执行分支 -----------------------------------------------

    async def _run_identity(
            self,
            plan: ModeExecutionPlan,
            task: str,
            invoke: StageInvoker,
            traces: list[StageTrace],
    ) -> str:
        """``SINGLE`` / ``DELEGATE`` / ``SHARED_BOARD``：单阶段直调。"""
        slot = plan.root_slot or ""
        return await self._call_one(slot, plan.root_slot or "root", task, invoke, traces)

    async def _run_serial(
            self,
            plan: ModeExecutionPlan,
            task: str,
            invoke: StageInvoker,
            traces: list[StageTrace],
    ) -> str:
        """``SERIAL``：阶段串行，上一阶段输出作为下一阶段输入的一部分。"""
        current = task
        for index, stage in enumerate(plan.stages):
            payload = current if index == 0 else self._chain_prompt(plan.mode, stage.role, current)
            output = await self._call_one(stage.slot_name, stage.role, payload, invoke, traces)
            if output:
                current = output
            elif index > 0:
                # 某阶段无输出：保留上一阶段产物，不至于把整条流水线的上下文丢掉
                logger.warning("%spipeline stage %r produced no output", _LOG_PREFIX, stage.slot_name)
        return current

    async def _run_fanout(
            self,
            plan: ModeExecutionPlan,
            task: str,
            invoke: StageInvoker,
            traces: list[StageTrace],
    ) -> str:
        """``FANOUT``：worker 真并发（``asyncio.gather``）后聚合。"""
        root_output = await self._call_one(plan.root_slot, "dispatcher", task, invoke, traces)

        workers = [name for name in plan.worker_slots if name]
        if not workers:
            return root_output

        semaphore = asyncio.Semaphore(plan.max_parallelism) if plan.max_parallelism > 0 else None

        async def one(name: str) -> str:
            payload = f"{task}\n\n【调度结果】\n{root_output}" if root_output else task
            if semaphore is None:
                return await invoke(name, payload)
            async with semaphore:
                return await invoke(name, payload)

        results = await asyncio.gather(*(one(name) for name in workers), return_exceptions=True)

        collected: list[str] = []
        for name, value in zip(workers, results):
            if isinstance(value, BaseException):
                traces.append(StageTrace(name, "worker", False, 0, 0, f"{type(value).__name__}: {value}"))
                continue
            collected.append(self._label_output(name, str(value)))
            traces.append(StageTrace(name, "worker", True, 0, len(str(value)), ""))

        if plan.aggregation == Aggregation.CONCAT:
            body = "\n\n".join(collected)
        else:
            # RANKED：长输出通常信息量更大，先长的；稳定排序避免抖动
            collected.sort(key=len, reverse=True)
            body = "\n\n".join(collected)
        return f"{root_output}\n\n【汇总】\n{body}" if root_output else body

    async def _run_round_robin(
            self,
            plan: ModeExecutionPlan,
            task: str,
            invoke: StageInvoker,
            traces: list[StageTrace],
    ) -> str:
        """``ROUND_ROBIN``：正反方交替多轮，最后裁判汇总。"""
        debaters = [name for name in plan.worker_slots if name]
        if not debaters:
            return await self._run_identity(plan, task, invoke, traces)

        last_by_side: dict[str, str] = {}
        transcript: list[str] = []

        for round_index in range(1, max(1, plan.rounds) + 1):
            for side in debaters:
                opponent_args = "\n".join(
                    self._label_output(other, text)
                    for other, text in last_by_side.items()
                    if other != side and text
                )
                payload = task
                if opponent_args:
                    payload = (
                        f"{task}\n\n【第 {round_index - 1} 轮对方论证】\n{opponent_args}\n\n"
                        f"请站在「{side}」立场给出第 {round_index} 轮论证，并回应对方要点。"
                    )
                elif round_index > 1:
                    payload = f"{task}\n\n请站在「{side}」立场给出第 {round_index} 轮论证。"
                else:
                    payload = f"{task}\n\n请站在「{side}」立场给出首轮论证。"

                output = await self._call_one(side, f"debater:{side}", payload, invoke, traces)
                last_by_side[side] = output or ""
                transcript.append(f"第 {round_index} 轮 · {side}:\n{output}")

        transcript_text = "\n\n".join(transcript)
        if plan.aggregation != Aggregation.JUDGE or not plan.root_slot:
            return transcript_text

        verdict_payload = (
            f"{task}\n\n【辩论记录】\n{transcript_text}\n\n"
            f"请以「{plan.root_slot}」身份裁决：给出结论与理由，指出双方论据的可采信部分。"
        )
        verdict = await self._call_one(plan.root_slot, "judge", verdict_payload, invoke, traces)
        return f"{transcript_text}\n\n【裁决 · {plan.root_slot}】\n{verdict}"

    async def _run_supervised(
            self,
            plan: ModeExecutionPlan,
            task: str,
            invoke: StageInvoker,
            traces: list[StageTrace],
            approve: ApprovalGate | None,
    ) -> str:
        """``SUPERVISED``：规划 -> 审批 -> 执行。审批未通过则不启动执行槽位。"""
        planner_stage = next((s for s in plan.stages if s.role == "planner"), None)
        executor_stage = next((s for s in plan.stages if s.role == "executor"), None)

        plan_text = task
        if planner_stage is not None:
            plan_text = await self._call_one(
                planner_stage.slot_name, planner_stage.role, task, invoke, traces
            ) or task

        approved = True
        if approve is not None:
            try:
                approved = bool(await approve(plan_text))
            except Exception as exc:
                logger.warning("%sapproval gate failed: %s", _LOG_PREFIX, exc)
                approved = False
            traces.append(
                StageTrace("(approval)", "gate", approved, 0, len(plan_text), "" if approved else "rejected")
            )

        if not approved:
            return f"{plan_text}\n\n【计划未通过审批，执行阶段已终止】"

        executor = executor_stage.slot_name if executor_stage else next(iter(plan.worker_slots), "")
        if not executor:
            return plan_text
        return await self._call_one(
            executor,
            "executor",
            f"{task}\n\n【已批准的计划】\n{plan_text}",
            invoke,
            traces,
        )

    # -- 小工具 -------------------------------------------------------------

    async def _call_one(
            self,
            slot_name: str,
            role: str,
            payload: str,
            invoke: StageInvoker,
            traces: list[StageTrace],
    ) -> str:
        """调用一个阶段，并把它记进 ``traces``；异常降级为空串而不是抛出。"""
        import time

        if not slot_name:
            traces.append(StageTrace(slot_name, role, False, 0, 0, "empty slot"))
            return ""

        started = time.perf_counter()
        try:
            output = await invoke(slot_name, payload)
        except Exception as exc:
            traces.append(
                StageTrace(
                    slot_name, role, False,
                    int((time.perf_counter() - started) * 1000), 0,
                    f"{type(exc).__name__}: {exc}",
                )
            )
            logger.warning("%sstage %r failed: %s", _LOG_PREFIX, slot_name, exc)
            return ""

        text = output or ""
        traces.append(
            StageTrace(
                slot_name, role, True,
                int((time.perf_counter() - started) * 1000), len(text),
            )
        )
        return text

    @staticmethod
    def _chain_prompt(mode: str, role: str, previous_output: str) -> str:
        """流水线下一阶段的任务包装：显式带上上一阶段产物。"""
        return f"【来自 {mode} 上一阶段({role})的产物】\n{previous_output}\n\n请在此基础上继续推进。"

    @staticmethod
    def _label_output(name: str, text: str) -> str:
        return f"[{name}]\n{text}"
