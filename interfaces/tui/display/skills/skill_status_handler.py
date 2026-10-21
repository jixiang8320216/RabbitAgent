from typing import TYPE_CHECKING, Any, Optional
from domains.agent.domain.aggregates.react_agent import  ReActAgent



class SkillStatusHandler:

    def __init__(self,
        command_registry:Optional[Any]=None,
        agent:Optional[ReActAgent]=None
        )->None:
        self.command_registry = command_registry
        self.agent = agent
        self._cached_status:Optional[str]=None
        self._last_update:Optional[float]=None