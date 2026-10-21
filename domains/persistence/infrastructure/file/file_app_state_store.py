import pathlib
import concurrent

class FileAppStateStore:

   def __init__(
       self,
       apps_dir: pathlib.Path,
           *,
       runtime_lease_executor: concurrent.futures.Executor|None = None,
   ) -> None:
       self._dir = pathlib.Path(apps_dir)
       self._dir.mkdir(parents=True, exist_ok=True)
       self._runtime_lease_executor = runtime_lease_executor