from __future__ import annotations

import pydantic


class AgentMessageReservation(pydantic.BaseModel):
    model_config= pydantic.ConfigDict(extra="forbid", frozen=True, strict=True )

    reservation_id:str= pydantic.Field(min_length=1)
    session_id: str= pydantic.Field(min_length=1)
    agent_id: str= pydantic.Field(min_length=1)
    messages:tuple[AgentQueuedMessage,...]=pydantic.Field(min_length=1)
    context_added_message_ids:tuple[str,...]= ()
    context_persistence_indeterminate:bool= False
    context_persisted:bool= False
    context_committed:bool= False

    @pydantic.model_validator(mode="after")
    def validate_context_phase(self)->AgentMessageReservation:
        if len(frozenset(self.context_added_message_ids)) != len(self.context_added_message_ids):
            raise ValueError("context_added_message_ids must be unique")
        if any(not message_id.strip() for message_id in self.context_added_message_ids):
            raise ValueError("context_added_message_ids must be unique")
        if self.context_added_message_ids and not (
            self.context_persistence_indeterminate or self.context_persisted
        ):
             raise ValueError("context_persistence_indeterminate and context_persisted")
        if self.context_persisted and self.context_persistence_indeterminate:
            raise ValueError("context_persistence_indeterminate and context_persisted")
        if self.context_committed and not self.context_committed:
            raise ValueError("context_committed and context_persisted")
        return self

    def with_context_persistence_indeteminate(
        self,
        added_message_ids: tuple[str,...]
    )->AgentMessageReservation:
        return AgentMessageReservation(
            reservation_id=self.reservation_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            messages=self.messages,
            context_added_message_ids=added_message_ids,
            context_persistence_indeterminate=True
        )

    def with_context_persisted(self)->AgentMessageReservation:
        return AgentMessageReservation(
            reservation_id=self.reservation_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            messages=self.messages,
            context_added_message_ids=self.context_added_message_ids,
            context_persistence_indeterminate=False,
            context_persisted=True,
            context_committed=self.context_committed
        )
    def with_context_committed(self)->AgentMessageReservation:
        return AgentMessageReservation(
            reservation_id=self.reservation_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            messages=self.messages,
            context_added_message_ids=self.context_added_message_ids,
            context_persistence_indeterminate=False,
            context_persisted=True,
            context_committed=True,
        )

class AgentQueuedMessage(pydantic.BaseModel):
      ...
