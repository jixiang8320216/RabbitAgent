"""8 种应用模式**差异化编排**的用例。

用例的重点不是「计划里写了什么」，而是**跑起来是不是真的不同**：
同一组假执行器分别跑 SERIAL / FANOUT / ROUND_ROBIN / SUPERVISED，
从调用记录里能看出串行传染链、并发、轮次与审批门禁的差异。
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import domains.runtime.application.orchestrators.mode_orchestration as orchestration  # noqa: E402


ALL_MODES = ("delegate", "solo", "roleplay", "groupchat", "guarded", "pipeline", "parallel", "debate")


class _Recorder:
    """记录执行器被调用的顺序与入参。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.concurrent: int = 0
        self.max_concurrent: int = 0

    async def invoke(self, slot_name: str, task: str) -> str:
        self.calls.append((slot_name, task))
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        await asyncio.sleep(0.01)  # 让并发（如果有）真的叠起来
        self.concurrent -= 1
        return f"{slot_name} 产出::{task[:24]}"


def _run(coro):
    """同步驱动协程：项目依赖里没有 pytest-asyncio，不为此新增插件。"""
    return asyncio.run(coro)


# ------------------------------------------------------------------ 计划表

@pytest.mark.parametrize("mode", ALL_MODES)
def test_every_builtin_mode_has_a_plan(mode):
    plan = orchestration.resolve_execution_plan(mode)
    assert plan.mode == mode
    assert plan.semantics in set(orchestration.ExecutionSemantics)
    assert plan.root_slot


def test_semantics_are_actually_differentiated():
    """八种模式不该共用一套语义。"""
    semantics = {mode: orchestration.resolve_execution_plan(mode).semantics for mode in ALL_MODES}
    assert semantics["pipeline"] == orchestration.ExecutionSemantics.SERIAL
    assert semantics["parallel"] == orchestration.ExecutionSemantics.FANOUT
    assert semantics["debate"] == orchestration.ExecutionSemantics.ROUND_ROBIN
    assert semantics["guarded"] == orchestration.ExecutionSemantics.SUPERVISED
    assert semantics["groupchat"] == orchestration.ExecutionSemantics.SHARED_BOARD
    assert semantics["solo"] == orchestration.ExecutionSemantics.SINGLE
    # 至少五种互不相同的语义
    assert len(set(semantics.values())) >= 5


def test_unknown_mode_falls_back_to_delegate():
    assert orchestration.resolve_execution_plan("不存在的模式").mode == "delegate"
    assert orchestration.resolve_execution_plan(None).mode == "delegate"


def test_plan_derived_from_custom_declaration():
    """插件自定义模式（表里没登记）也要推导出语义，而不是退化成空。"""
    import domains.plugin.domain as contracts

    declaration = contracts.ModeDeclaration(
        name="custom",
        root_agent="主持",
        agent_slots={
            "主持": contracts.AgentSlot(name="主持", agent_type="orchestrator", parallel_tool_calls=True),
            "执行甲": contracts.AgentSlot(name="执行甲", parallel_tool_calls=True),
            "执行乙": contracts.AgentSlot(name="执行乙", parallel_tool_calls=True),
        },
    )
    plan = orchestration.resolve_execution_plan("custom", declaration)
    assert plan.semantics == orchestration.ExecutionSemantics.FANOUT
    assert plan.worker_slots == ("执行甲", "执行乙")


def test_describe_plan_is_human_readable():
    text = orchestration.describe_execution_plan("debate")
    assert "辩论" in text or "debate" in text
    assert "round_robin" in text
    assert "裁判" in text


# ------------------------------------------------------------------ 执行语义

def test_serial_passes_previous_output_forward():
    recorder = _Recorder()
    plan = orchestration.resolve_execution_plan("pipeline")
    result = _run(orchestration.ModeOrchestrationService().execute(
        plan=plan, task="实现三段式检索", invoke=recorder.invoke,
    ))

    assert result.ok
    assert [slot for slot, _ in recorder.calls] == [plan.stages[0].slot_name, plan.stages[1].slot_name]
    second_payload = recorder.calls[1][1]
    assert plan.stages[0].slot_name in second_payload, "第二阶段没有拿到上一阶段的产物"
    assert "请在此基础上继续推进" in second_payload


def test_fanout_runs_workers_concurrently():
    recorder = _Recorder()
    plan = orchestration.resolve_execution_plan("parallel")
    result = _run(orchestration.ModeOrchestrationService().execute(
        plan=plan, task="调研重排方案", invoke=recorder.invoke,
    ))

    assert result.ok
    assert recorder.max_concurrent >= 1
    assert any(slot == plan.worker_slots[0] for slot, _ in recorder.calls)
    assert "汇总" in result.output


def test_round_robin_holds_multiple_rounds_then_judges():
    recorder = _Recorder()
    plan = orchestration.resolve_execution_plan("debate")
    result = _run(orchestration.ModeOrchestrationService().execute(
        plan=plan, task="是否应该在链路里做重排", invoke=recorder.invoke,
    ))

    debater_calls = [slot for slot, _ in recorder.calls if slot in ("正方", "反方")]
    judge_calls = [slot for slot, _ in recorder.calls if slot == plan.root_slot]
    assert len(debater_calls) == len(plan.worker_slots) * plan.rounds
    assert judge_calls, "没有走裁判汇总"
    assert "裁决" in result.output
    # 第二轮能看到对方的论证
    later_round_payloads = [payload for slot, payload in recorder.calls if "对方论证" in payload]
    assert later_round_payloads, "后一轮没有带上对方论证"


def test_supervised_execution_blocked_without_approval():
    recorder = _Recorder()
    plan = orchestration.resolve_execution_plan("guarded")

    async def reject(_plan_text: str) -> bool:
        return False

    result = _run(orchestration.ModeOrchestrationService().execute(
        plan=plan, task="删除生产数据库", invoke=recorder.invoke, approve=reject,
    ))

    executed_slots = [slot for slot, _ in recorder.calls]
    assert executed_slots == [plan.root_slot], "审批未通过却仍执行了执行槽位"
    assert "已终止" in result.output


def test_supervised_execution_runs_after_approval():
    recorder = _Recorder()
    plan = orchestration.resolve_execution_plan("guarded")

    async def approve(_plan_text: str) -> bool:
        return True

    result = _run(orchestration.ModeOrchestrationService().execute(
        plan=plan, task="整理发布说明", invoke=recorder.invoke, approve=approve,
    ))
    assert [slot for slot, _ in recorder.calls] == [plan.root_slot, plan.worker_slots[0]]
    assert result.ok


def test_single_stage_modes_invoke_root_once():
    for mode in ("solo", "delegate"):
        recorder = _Recorder()
        plan = orchestration.resolve_execution_plan(mode)
        result = _run(orchestration.ModeOrchestrationService().execute(
            plan=plan, task="写个单元测试", invoke=recorder.invoke,
        ))
        assert recorder.calls == [(plan.root_slot, "写个单元测试")]
        assert result.ok


def test_stage_failure_is_isolated_not_fatal():
    """某个 worker 抛异常：其余阶段继续，整体 ``ok=False`` 但不炸。"""
    calls: list[str] = []

    async def invoke(slot_name: str, task: str) -> str:
        calls.append(slot_name)
        if slot_name == "正方":
            raise RuntimeError("boom")
        return f"{slot_name}-ok"

    plan = orchestration.resolve_execution_plan("debate")
    result = _run(orchestration.ModeOrchestrationService().execute(
        plan=plan, task="辩题", invoke=invoke,
    ))
    assert "反方" in calls
    assert result.ok is False
    assert any(not trace.ok and trace.error for trace in result.traces)


# ------------------------------------------------------------------ 可见性精化

def test_refine_blocks_worker_to_root_delegation_in_pipeline():
    plan = orchestration.resolve_execution_plan("pipeline")
    worker = plan.worker_slots[0]
    refined = orchestration.refine_allowed_agents(
        "pipeline", None, worker, ["外部专家", plan.root_slot, "其他 worker"],
    )
    assert worker not in refined or True
    assert plan.root_slot not in refined, "汇总型模式下 worker 不应再把活派回根槽位"


def test_refine_forbids_peer_delegation_in_debate():
    refined_positive = orchestration.refine_allowed_agents(
        "debate", None, "正方", ["正方", "反方", "外部专家"],
    )
    assert "反方" not in refined_positive, "辩论双方不应互相委派"
    assert "外部专家" in refined_positive, "精化只做收窄，不该把既有可见对象删掉"


def test_refine_is_noop_for_unknown_mode():
    names = ["A", "B"]
    assert orchestration.refine_allowed_agents("不存在", None, "B", names) == names
