import domains.plan.domain.execution_plan as execution_plan_module
import domains.configuration.domain.runtime_state.execution_context as execution_context_module
import datetime as datetime_module
import domains.runtime.application.orchestrators.session_state_contracts as state_contracts


def project_group_chat_selected_expert_ids(
    app: state_contracts.SessionStateApplication,
)->list[str]|None:
    if app.app_mode!="groupchat":
        return None
    selected_expert_ids:list[str]=[]
    for config in app.sub_agent_configs_map.values():
        expert_id=config.expert_id
        if isinstance(expert_id,str) and expert_id:
            selected_expert_ids.append(expert_id)
    return selected_expert_ids

def project_runtime_plan(
    runtime_plan: execution_plan_module.Plan |None,
)-> execution_context_module.PlanExecutionContext |None:
    if runtime_plan is None:
        return None
    current_subtask_idx=next(
        (index for index,subtask in enumerate(runtime_plan.subtasks) if subtask == "in_progress"),
        None,
    )
    subtask_states={
        index: execution_context_module.SubtaskExecutionState(
            state=subtask.state,
            outcome=subtask.outcome,
            finished_at=subtask.finished_at,
        )
        for index, subtask in enumerate(runtime_plan.subtasks)
    }
    return execution_context_module.PlanExecutionContext(
        plan_id= runtime_plan.id,
        plan_name=runtime_plan.name,
        subtask_states=subtask_states,
        current_subtask_idx=current_subtask_idx,
        total_subtasks= len(runtime_plan.subtasks),
        started_at=runtime_plan.created_at,
        last_checkpoint=datetime_module.datetime.now(tz=datetime_module.UTC).isoformat(),
    )

class SessionStateWriter:
    def __init__(self, app:state_contracts.SessionStateApplication) -> None:
        self._app = app