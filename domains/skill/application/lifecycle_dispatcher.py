from __future__ import annotations

from .idle_gate import IdleGate
from collections.abc import Callable
from domains.skill.domain.heartbeat import SelfEvolutionHeartbeatRequest
from domains.skill.domain.runs import SelfEvolutionRun
from domains.skill.application.lifecycle_counter_ports import LifecycleCounterStore

HeartbeatHandler =Callable[[SelfEvolutionHeartbeatRequest],SelfEvolutionRun]


class LifecycleCounters:

    def __init__(
        self,
            *,
        store:LifecycleCounterStore,
        turn_internal_threshold:int =10,
        iteration_internal_threshold:int =10,
    )->None:
        if turn_internal_threshold <0 or iteration_internal_threshold <=0:
            raise ValueError("turn_internal_threshold and iteration_internal_threshold must be positive")
        self._store = store
        self.turn_internal_threshold = turn_internal_threshold
        self.iteration_internal_threshold = iteration_internal_threshold



class  SelfEvolutionLifecycleDispatcher:
    def __init__(
        self,
        *,
        heartbeat_handler: HeartbeatHandler,
        idle_gate: IdleGate | None = None,
        counters: LifecycleCounters,
        debounce_seconds: float | None = None,
        clock:Callable[[],float] | None = None,
        outcome_emitter: OutcomeEmitter|None = None,
        )->None:
        #todo
        pass