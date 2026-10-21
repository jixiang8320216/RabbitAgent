from domains.observability.infrastructure.observers.noop_observer import NoOpObserver
from domains.skill import SelfEvolutionLifecycleDispatcher
from collections.abc import Callable

LifecyceBackgroundSubmitter= Callable[[str,str|None,Callable[[],object]], None]


class LifecycleObserver(NoOpObserver):
    """"""

    def __init__(
        self,
        dispatcher:SelfEvolutionLifecycleDispatcher,
        *,
        background_submitter: LifecyceBackgroundSubmitter|None = None,
    )->None:
        super().__init__()
        self._dispatcher = dispatcher
        self._background_submitter = background_submitter
