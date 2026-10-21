import pathlib

from . import version_control

def build_workspace_version_service(
    *,
    workspace_root: str| pathlib.Path|None=None,
    app_id: str|None=None,
    actor:str="agent",
)->version_control.WorkspaceVersionService:
    explicit_root = workspace_root
    #todo