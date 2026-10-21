"""执行上下文领域模型。

``ExecutionContext`` 承载一次会话的运行态快照（会话 id、创建/更新时间、工作目录、
应用模式、计划上下文、群聊选中专家等）；``PlanExecutionContext`` / ``SubtaskExecutionState``
是计划执行态的子结构，由 ``session_state_writer.project_runtime_plan`` 在恢复/计划
路径上构造。
"""

from __future__ import annotations

import typing

import pydantic


class SubtaskExecutionState(pydantic.BaseModel):
    """单个子任务的执行态。"""

    state: str | None = None
    outcome: str | None = None
    finished_at: str | None = None


class PlanExecutionContext(pydantic.BaseModel):
    """计划（plan）的执行态投影。"""

    plan_id: str | None = None
    plan_name: str | None = None
    subtask_states: dict[int, SubtaskExecutionState] = pydantic.Field(default_factory=dict)
    current_subtask_idx: int | None = None
    total_subtasks: int = 0
    started_at: str | None = None
    last_checkpoint: str | None = None


class ExecutionContext(pydantic.BaseModel):
    """一次会话的运行态快照。"""

    session_id: str
    created_at: str
    last_updated: str
    working_directory: str
    app_mode: typing.Any | None = None
    current_plan: PlanExecutionContext | None = None
    historical_plans: list[PlanExecutionContext] = pydantic.Field(default_factory=list)
    group_chat_selected_expert_ids: list[str] | None = None
    group_chat_selected_skills: typing.Any | None = None
    agent_config_id: str | None = None
    role_selection: typing.Any | None = None
    agent_config_snapshots: dict[str, object] = pydantic.Field(default_factory=dict)
    # 由 agent 生命周期钩子写入：根 agent 当前这一轮 invocation 的标识，以及
    # plan 模式下各 plan agent 的调用记录。``agent_call_lifecycle_hooks`` 会直接对
    # ``app.execution_context`` 赋值/append，缺字段会抛 pydantic 的
    # ``"ExecutionContext" object has no field ...`` 错误，阻断整条 turn。
    current_entry_agent_instance_id: str | None = None
    plan_agent_invocations: list[dict[str, pydantic.JsonValue]] = pydantic.Field(
        default_factory=list
    )


__all__ = [
    "SubtaskExecutionState",
    "PlanExecutionContext",
    "ExecutionContext",
]
