
import collections.abc
import collections.abc as abc
import asyncio
class SessionIncrementalSaveCoordinator:

    def __init__(self,save_operation:collections.abc.Callable[[str],abc.Awaitable[None]]):
        self._save_operation = save_operation
        self._requested_generation=0
        self._settled_genration=0
        self._session_id:str |None=None
        self._worker: asyncio.Task[None] |None = None
        self._waiters: dict[int,asyncio.Future[None]]={}