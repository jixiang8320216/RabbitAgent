
from domains.skill import SelfEvolutionLifecycleDispatcher
from domains.agent import get_lifecycle_notifier,set_lifecycle_notifier,LifecycleNotifierProtocol
import logging
from collections.abc import Callable
from typing import Protocol,cast,runtime_checkable
from concurrent.futures import Future, ThreadPoolExecutor
from domains.skill.infrastructure.events.lifecycle_observer import LifecycleObserver

logger = logging.getLogger(__name__)

type LifecycleOperation = Callable[[],object]

@runtime_checkable
class _ShutdownNotifier(Protocol):
    def shutdown(self, *, wait: bool) -> None:...


class HarnessLifecycleNotifier:

    def __init__(
        self,
        dispatcher: SelfEvolutionLifecycleDispatcher,
        *,
        executor: ThreadPoolExecutor|None = None,
    ) -> None:
        self.dispatcher = dispatcher
        if executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='rabbit-lifecycle')
            self._owns_executor = True
        else:
            self._executor = executor
            self._owns_executor = False
        self._observer= LifecycleObserver(
            dispatcher,
            background_submitter = self._submit_observer_background,
        )

    def lifecycle_observer(self) -> LifecycleObserver:
        """对外暴露生命周期观察者。

        ``rabbit_application_composition._resolve_lifecycle_observer`` 调的是
        ``notifier.lifecycle_observer()``，而本类原先只有 ``_observer`` 私有属性，
        于是每次启动都抛 ``AttributeError: 'HarnessLifecycleNotifier' object has
        no attribute 'lifecycle_observer'``，生命周期观察者永远进不了 composite。
        """
        return self._observer

    def _submit_observer_background(
        self,
        label:str,
        session_id:str|None,
        project_name:str|None,
        operation:LifecycleOperation
    )-> None:
        future = self._executor.submit(self._invoker_logged, label, session_id,project_name,operation)
        future.add_done_callback(lambda f: self._ont_background_done(f,label,session_id))

    def _invoker_logged(
        self,
        label:str,
        session_id:str|None,
        project_name:str|None,
        operation:LifecycleOperation,
    )-> object|None:
        """在工作线程里执行生命周期操作；异常只记日志，不外抛到 future。"""
        try:
            return operation()
        except Exception:
            logger.exception(
                "lifecycle background operation failed: %s (session=%s, project=%s)",
                label, session_id, project_name,
            )
            return None

    def _ont_background_done(
        self,
        future:"Future[object]",
        label:str,
        session_id:str|None,
    )-> None:
        """后台任务结束回调：取出结果以便异常不被静默丢弃。"""
        try:
            future.result()
        except Exception:
            logger.exception(
                "lifecycle background task failed: %s (session=%s)", label, session_id
            )




def instll_lifecycle_notifier(
    dispatcher:SelfEvolutionLifecycleDispatcher,
)-> HarnessLifecycleNotifier:
    previous = get_lifecycle_notifier()
    notifier = HarnessLifecycleNotifier(dispatcher)
    set_lifecycle_notifier(cast(LifecycleNotifierProtocol, notifier))
    if isinstance(previous, _ShutdownNotifier) and previous is not notifier:
        try:
            previous.shutdown(wait=True)
        except Exception:
            logger.exception("Shutdown notifier failed")
    return notifier