from __future__ import annotations

import collections
import typing
import domains.agent.domain.aggregates.cancellation as cancellation

import pydantic
import datetime as datetime_module
import domains.agent.domain.value_objects.conversation_context as conversation_context
import domains.agent.domain.value_objects.tool_response as tool_response
import domains.agent.domain.value_objects.message as message

type JsonObject=dict[str, pydantic.JsonValue]

class AgentContextPersistenceProtocol(typing.Protocol):

    async def __call__(
        self,
        agent_id:str,
        expected_context: conversation_context.ContextState,
    )->None:...

class QueuedMessageAdmissionFenceProtocol(typing.Protocol):
    @property
    def operation_id(self)->str:...

    @property
    def client_message_id(self)->str:...

    @property
    def epoch(self)->int:...

    @property
    def owner_token(self)->str:...


class QueuedMessageMetadataProtocol(typing.Protocol):
    @property
    def queue_id(self)->str|None:...

    @property
    def queue_king(self)->typing.Literal["pending_user_message"]|None:...

    @property
    def client_message_id(self)->str|None:...

    @property
    def invocation_id(self)->str|None:...

    @property
    def immediate(self)->bool|None:...

    @property
    def admission_fence(self)->QueuedMessageAdmissionFenceProtocol:...

    def to_json_object(self)->JsonObject:...

class QueuedMessageProtocol(typing.Protocol):

    @property
    def session_id(self)->str|None:...

    @property
    def content(self)->str|None:...

    @property
    def source(self)->str|None:...

    @property
    def delivery_id(self)->str|None:...

    @property
    def timestamp(self)->datetime_module.datetime:...

    @property
    def metadata(self)->QueuedMessageMetadataProtocol:...



class QueuedMessageReservationProtocol(typing.Protocol):
    @property
    def reservation_id(self)->str:...

    @property
    def messages(self)->tuple[QueuedAgentMessageProtocol,...]:...

    @property
    def content_added_message_ids(self)->tuple[str,...]:...

    @property
    def content_persistence_indeterminate(self)->bool:...

    @property
    def content_persisted(self)->bool:...

    @property
    def content_committed(self)->bool:...

@typing.runtime_checkable
class MessageQueueProtocol(typing.Protocol):

    def enqueue(
        self,
        session_id:str,
        agent_id:str,
        content:str,
        source:str="unknown",
    )->bool:...

    def consume(
        self,
        session_id:str,
        agent_id:str,
    )->collections.abc.Sequence[QueuedAgentMessageProtocol]:...

    def consume_sources(
        self,
        session_id:str,
        agent_id:str,
        source:collections.abc.Collection[str],
    )->collections.abc.Sequence[QueuedAgentMessageProtocol]:...

    def reserve(
        self,
        session_id:str,
        agent_id:str,
    )->QueuedMessageReservationProtocol:...


    async def prepare_reservation(
        self,
        reservation_id:str,
    )->bool:...

    async def complete_reservation_admission(
        self,
        reservation_id:str,
    )->bool:...

    def mark_reservation_context_persistence_indeterminate(
            self,
            reservation_id: str,
            added_message_ids: tuple[str,...],
    ) -> bool: ...

    def mark_reservation_context_persisted(
            self,
            reservation_id: str,
    ) -> bool: ...

    def restore_indeterminate_reservation(
            self,
            reservation_id: str,
    ) -> bool: ...

    def release_reservation_owner(
            self,
            reservation_id: str,
    ) -> bool: ...

    def commit_reservation(
            self,
            reservation_id: str,
    ) -> bool: ...

    def restore_reservation(
            self,
            reservation_id: str,
    ) -> bool: ...

class MessageQueueFactoryProtocol(typing.Protocol):

    def __call__(self)->MessageQueueProtocol:...

type AgentTaskFactory= collections.abc.Callable[[],typing.Awaitable[object]]

class AgentTaskSchedulerProtocol(typing.Protocol):

    @property
    def sync_wait_timeout_seconds(
            self,
    )->float:...

    async def spawn(
        self,
        agent_id:str,
        agent_func:AgentTaskFactory,
        parent_id:str|None=None,
        sync:bool=False,
        task_description:str|None=None,
        skill_name:str|None=None,
        )->str:...

    async def wait_for_agent(
        self,
        agent_id:str,
        timeout:float,
        *,
        demote_on_timeout:bool=False,
        )->str|None:...

    def get_result_text(self,agent_id:str)->str|None:...

class AgentSchedulerResolverProtocol(typing.Protocol):


    def __call__(self)->AgentTaskSchedulerProtocol:...

class PlanningRuntimeProtocol(typing.Protocol):
    def state_dict(self)->dict[str,object]:...

    def load_state_dict(self,data: collections.abc.Mapping[str,object])->None:...

class QueuedAgentMessageProtocol:
    @property
    def session_id(self)->str|None:...


class LifecycleOutcomeStatusProtocol(typing.Protocol):
    @property
    def kind(self)->str:...


class RuntimeToolRegistrationProtocol(typing.Protocol):

    @property
    def is_agent(self)->bool:
        ...

    @property
    def preset_kwargs(self)->collections.abc.Mapping[str,object]:...

@typing.runtime_checkable
class ToolClassificationResolverProtocol(typing.Protocol):

    def resolve_tool_classification(self,
        tool_name:str,
    )-> agent_execution.ToolClassification|None:
        ...

class ToolTelemetryEmitterProtocol(typing.Protocol):
    def __call__(
        self,
        *,
        tool_name:str,
        status:str,
        duration_ms:int|None=None,
        tool_type:str|None=None,
        mcp_server_name:str|None=None,
        error_type:str|None=None,
        tool_metadata:dict[str,str]|None=None,
        )->None:...

class ToolObservabilityMetadataResolverProtocol(typing.Protocol):

    def __all__(
        self,
        *,
        tool_name:str,
        tool_input:JsonObject,
    )->dict[str,str]|None:...

class ToolkitProtocol(typing.Protocol):

    @property
    def tools(self)-> collections.abc.Mapping[str,RuntimeToolRegistrationProtocol]:...


    def get_json_schema(self,active_only: bool=True)->list[JsonObject]:...

    def update_tool_groups(self,group_names:list[str], active:bool)->None:...

    async def call_tool_function(self,
        tool_call:message.ToolUseBlock,
    )-> typing.AsyncGenerator[tool_response.ToolResponse,None]:...


@typing.runtime_checkable
class InterruptCheckerProtocol(typing.Protocol):
    def check_interrupt(self, session_id: str) -> bool:...

    def get_turn_scope(self, session_id: str) -> cancellation.TurnCancellationScope|None:...

@typing.runtime_checkable
class ToolArgsSummaryResolverProtocol(typing.Protocol):
    def resolve_args_summary(self, tool_name: str, tool_input:JsonObject) -> str|None:...


@typing.runtime_checkable
class LifecycleStatusNotifierProtocol(typing.Protocol):
    def notify_outcome(self, outcome:LifecycleOutcomeStatusProtocol)->None:
        ...

@typing.runtime_checkable
class LifecycleNotifierProtocol(typing.Protocol):
    def notify_user_turn_finalized(
        self,
        *,
        session_id:str|None,
        project_home:str|None=None,
        project_home_red:str|None=None,
        evidence: collections.abc.Mapping[str, pydantic.JsonValue] | None=None,
         )->None:...