from pathlib import Path
import json
from typing import Never
from collections.abc import Callable
from domains.configuration.domain.protocol.project_layout_impl import ProjectLayout
from domains.configuration import JsonObject
from domains.persistence.infrastructure.file import FileLockTimeoutError, FileLockUnsafePathError,file_lock,atomic_write_text
from pydantic import TypeAdapter, ValidationError

PROJECT_CONFIG_LOCK_TIMEOUT_SECONDS= 5.0

_JSON_OBJECT_ADAPTER =TypeAdapter(JsonObject)


class ProjectConfigInvalidError(RuntimeError):
      ...
class ProjectConfigBusyError(RuntimeError):
    ...

class ProjectConfigUnsafePathError(ProjectConfigInvalidError):
      ...

def read_project_config(project_home:str| Path) ->JsonObject:

    layout=_safe_project_layout(project_home)
    return _read_config_file(layout.config_file)


def update_project_config[ResultT](
    project_name:str|Path,
    mutate:Callable[[JsonObject],ResultT],
    *,
    timeout_seconds:float=PROJECT_CONFIG_LOCK_TIMEOUT_SECONDS,
)->ResultT:

    layout = _safe_project_layout(project_name)
    try:
        with file_lock(layout.config_lock_file, timeout_seconds):
            config = _read_config_file(layout.config_file)
            result = mutate(config)
            content=(
                json.dumps(
                    config,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                +"\n"
            )
            atomic_write_text(layout.config_file, content, encoding="utf-8")
            return result
    except FileLockTimeoutError as exc:
        raise ProjectConfigBusyError(str(exc))
    except FileLockUnsafePathError as exc:
        raise ProjectConfigUnsafePathError(str(exc)) from exc


def _safe_project_layout(project_name: str|Path)->ProjectLayout:
    project_path=Path(project_name)
    if not project_path.is_absolute():
        raise ProjectConfigUnsafePathError("project_name must be a absolute path")
    layout = ProjectLayout(project_home= project_path.resolve(strict=False))
    for path in (
        layout.rabbit_dir,
        layout.config_file,
        layout.config_lock_file,
    ):
        if _is_link_or_reparse_point(path):
            raise ProjectConfigUnsafePathError(
                f"Project config path must not be asymbolic link or reparse point:{path}"
            )
    return layout

def _is_link_or_reparse_point(path:Path)->bool:
    return path.is_symlink() or path.is_junction()

def _read_config_file(config_file:Path)->JsonObject:
    if not config_file.exists():
        return {}
    try:
        parsed: object = json.loads(
            config_file.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (ValueError, UnicodeDecodeError,ValidationError) as exc:
        raise ProjectConfigInvalidError(str(exc))
    if not isinstance(parsed, dict):
        raise ProjectConfigInvalidError(str(parsed))
    return _JSON_OBJECT_ADAPTER.validate_python(parsed)

def _reject_nonstandard_json_constant(value:str)->Never:
    raise ValueError(f"Non-standard JSON value {value}")
