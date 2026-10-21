import pathlib

import pydantic


class ProjectLayout(pydantic.BaseModel):

    model_config= pydantic.ConfigDict(frozen=True)

    project_home:pathlib.Path

    def __init__(self,project_home:str|pathlib.Path)->None:
        super().__init__(project_home=project_home)


    @property
    def rabbit_dir(self) -> pathlib.Path:
        return self.project_home / ".rabbit"

    def rabbit_path(self,*parts:str|pathlib.Path) -> pathlib.Path:
        return self.rabbit_dir.joinpath(*parts)

    @property
    def config_file(self) -> pathlib.Path:
        return self.rabbit_dir / "config.json"

    @property
    def config_lock_file(self) -> pathlib.Path:
        return self.rabbit_dir / "config.lock"

    @property
    def commands_dir(self) -> pathlib.Path:
        return self.rabbit_path("commands")

def project_layout(project_home:str|pathlib.Path) -> ProjectLayout|None:
    if project_home is None:
        return None
    if isinstance(project_home, str) and not project_home:
        return None
    return ProjectLayout(project_home)

