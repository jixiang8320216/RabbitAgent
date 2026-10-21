
import typing
import domains.plan.domain.execution_plan as execution_plan

class  PlanObserverPort(typing.Protocol):
    async def on_plan_update(self
        ,
        agent_name:str,
        plan_subtasks: list[execution_plan.Subtask],
        instance_id:str|None = None,
    )->None:
        ...


class  PlanHookManager:


    def __init__(
        self,
        observer: PlanObserverPort|None = None,
    )->None:
        self.observer = observer


