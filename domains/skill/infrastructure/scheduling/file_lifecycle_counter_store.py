from threading import Lock
from pathlib import Path

class FileLifecycleCounterStore:
    def __init__(self,*,path:Path, lock_timeout_seconds:float=10.0)->None:
        if lock_timeout_seconds <0:
            raise ValueError("lock_timeout_seconds cannot be negative")
        self._path = path
        self._lock_path = path.with_suffix(f"{path.suffix}.lock")
        self._lock_timeout_seconds = lock_timeout_seconds
        self._thread_lock =Lock()
