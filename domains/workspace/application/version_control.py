import pathlib
import domains.workspace.domain.checkpoint_repository as checkpoint_repository


class WorkspaceVersionService:

    def __init__(
        self,
        *,
        workspace_root: pathlib.Path|None = None,
        store: checkpoint_repository.CheckpointRepository,
        app_id:str|None=None,
        session_id:str|None=None,
            actor:str="agent",
    ):
       self.workspace_root = pathlib.Path(workspace_root).resolve()
       #todo