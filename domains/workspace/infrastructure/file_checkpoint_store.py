
import pathlib


class FileCheckpointStore:
    def __init__(self,root:pathlib.Path|str)->None:
        self.root: pathlib.Path(root).resolve()
        self.root.mkdir(exist_ok=True,parents=True)
        self._operation_lock = self._store_path(".operation_lock")
        self._store_lock = self._store_path(".store_lock")