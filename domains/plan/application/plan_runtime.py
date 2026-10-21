
import domains.plan.domain.execution_plan as execution_plan
import collections
import typing
import pydantic
import collections.abc as collections_abc
import domains.agent.domain.value_objects.message as message
import domains.agent.domain.value_objects.tool_response as tool_response


class PlanRuntimeState(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    current_plan: execution_plan.Plan|None=None
    history: list[execution_plan.Plan]= pydantic.Field(default_factory=list)

class PlanChangeHook(typing.Protocol):
    def __call__(
        self,
        runtime:PlanRuntime,
        current_plan: execution_plan.Plan|None,
    )->None:...


async def _execute_plan_change_hook(
    hook: PlanChangeHook,
    runtime: PlanRuntime,
    current_plan: execution_plan.Plan|None,
)->None:
    result= hook(runtime,current_plan)
    if result is not None:
        await result

class PlanRuntime:

    description:str=(
        "The plan-related tools.Activate this tool when you need to execute"
    )

    def __init__(self)->None:
        self.current_plan: execution_plan.Plan|None=None
        self._plan_change_hooks: collections.OrderedDict[str,PlanChangeHook]=collections.OrderedDict()
        self._history: list[execution_plan.Plan]=[]

    def register_plan_change_hook(
        self,
        hook_name:str,
        hook:PlanChangeHook,
    )->None:
        self._plan_change_hooks[hook_name]=hook

    def remove_plan_change_hook(self,
        hook_name:str,
    )->None:
       if hook_name in self._plan_change_hooks:
           self._plan_change_hooks.pop(hook_name)
       else:
           raise Exception(f"The plan hook {hook_name} was not registered")

    async def _trigger_plan_change_hooks(self)->None:
        for hook in self._plan_change_hooks.values():
            await _execute_plan_change_hook(hook,self,self.current_plan)

    def _require_current_plan(self)-> execution_plan.Plan:
        current_plan = self.current_plan
        if current_plan is None:
            raise ValueError("the current plan has not been set")
        return current_plan

    def _archive_plan(self, plan: execution_plan.Plan)->None:
        self._history.append(plan.model_copy(deep=True))


    def _find_historical_plan(self,plan_id:str)->execution_plan.Plan|None:
        for plan in reversed(self._history):
            if plan.id == plan_id:
                return plan.model_copy(deep=True)
        return None

    def list_historical_plan(self)->list[execution_plan.Plan]:
        return [plan.model_copy(deep=True) for plan in self._history]

    @staticmethod
    def _normalize_subtask_index(subtask_idx:int|str)->int|None:
        if isinstance(subtask_idx, int) and not isinstance(subtask_idx, bool):
            return subtask_idx
        if isinstance(subtask_idx, str):
            try:
                return int(subtask_idx)
            except ValueError:
                return None
        return None

    def list_tools(self)->list[typing.Callable[...,collections_abc.Awaitable[tool_response.ToolResponse]]]:
        return [
            self.view_subtasks,
            self.update_subtask_state,
            self.finish_subtask,
            self.create_plan,
            self.revise_current_plan,
            self.finish_plan,
            self.view_historical_plan,
            self.recover_historical_plan,
        ]

    async def create_plan(
        self,
        name:str,
        description:str,
        expected_outcome:str,
        subtasks: list[execution_plan.Subtask],
    )->tool_response.ToolResponse:

        plan=execution_plan.Plan(
            name=name,
            description=description,
            expected_outcome=expected_outcome,
            subtasks=subtasks,
        )

        current_plan=self.current_plan
        if current_plan is None:
            res=tool_response.ToolResponse(
                content=[message.TextBlock(type="text", text=f"Plan '{name}' created successfully")],
            )
        else:
            if current_plan.state not in ("done","abandon"):
                current_plan.finish(
                    "abandoned",
                    f"The plan was replaced by thr newly created plan with ID '{plan.id}' "
                )
            self._archieve_plan(plan)
            res=tool_response.ToolResponse(
                content=[
                    message.TextBlock(type="text", text=f"The current plan named '{current_plan.name}' is replced by the"
                                      f"newly created plan named '{name}'."),
                ]
            )
        self.current_plan=plan
        await self._trigger_plan_change_hooks()
        return res
















