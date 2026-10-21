from builtins import float
import logging

from domains.skill import LifecycleCounters, IdleGate,SelfEvolutionLifecycleDispatcher,HeartbeatHandler,LifecycleDispatchResult,project_lifecycle_outcome
from domains.skill.infrastructure.gateway.hermes_gateway import HermesGatewayService
from domains.skill.domain.models import  BackgroundReviewConfig
from domains.skill import SelfEvolutionRun,FileLifecycleCounterStore
from domains.agent import get_lifecycle_status_notifiers

logger = logging.getLogger(__name__)

def _gateway_heartbeat_handler(gateway: HermesGatewayService) -> HeartbeatHandler:
    def _handler(request: SelfEvolutionLifecycleDispatcher)->SelfEvolutionRun:
        request= gateway._inject_prior_feedback(request)
        return gateway.service().heartbeat(request)
    return _handler

def _registry_outcome_emitter(
    result: LifecycleDispatchResult,
    run: SelfEvolutionRun|None,
)->None:
    notifiers =get_lifecycle_status_notifiers()
    if not notifiers:
        return
    outcome = project_lifecycle_outcome(result,run)
    for notifier in notifiers:
        try:
            notifier.notify_outcome(outcome)
        except Exception as e:
            logger.exception(e)


def build_lifecycle_dispatcher(
    *,
    gateway: HermesGatewayService,
    counters: LifecycleCounters | None = None,
    idle_gate: IdleGate|None = None,
    debounce_seconds: float | None = None,
) -> SelfEvolutionLifecycleDispatcher:
    return SelfEvolutionLifecycleDispatcher(
        heartbeat_handler =_gateway_heartbeat_handler(gateway),
        idle_gate=idle_gate or IdleGate(BackgroundReviewConfig()),
        counters=counters
        or LifecycleCounters(
            store=FileLifecycleCounterStore(path=gateway.durable_root/"lifecycle-counters.json")
        ),
        debounce_seconds=debounce_seconds,
        outcome_emitter = _registry_outcome_emitter,
    )