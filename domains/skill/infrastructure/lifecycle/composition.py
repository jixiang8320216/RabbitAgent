
from domains.skill.infrastructure.gateway.hermes_gateway import HermesGatewayService
from domains.skill import LifecycleCounters,IdleGate
from .notifier import HarnessLifecycleNotifier,instll_lifecycle_notifier
from .dispatcher_factory import build_lifecycle_dispatcher

def  build_and_install_lifecycle_notifier(
    *,
    gateway: HermesGatewayService,
    counters: LifecycleCounters |None= None,
    idle_gate: IdleGate| None =None,
    debounce_seconds: float|None= None,
)->HarnessLifecycleNotifier:
    """Compose dispatcher + notifier and install in one step"""

    dispatcher = build_lifecycle_dispatcher(
        gateway = gateway,
        counters = counters,
        idle_gate = idle_gate,
        debounce_seconds = debounce_seconds
    )

    return instll_lifecycle_notifier(dispatcher)

__all__ =["build_and_install_lifecycle_notifier"]
