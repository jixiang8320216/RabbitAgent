from pathlib import Path

from typing import Literal
from enum import StrEnum
from pydantic import ConfigDict, BaseModel

class FlatSourceKind(StrEnum):
    APP="flat_app"
    AGENT="flat_agent"

class MigrationKind(StrEnum):
    FLAT_APP_V2="flat_app_v2"
    LEGACY_SESSION_LAZY="legacy_sesson_lazy"

class _MigrationModel(BaseModel):
    model_config= ConfigDict(extra="forbid")

class SourceFile(_MigrationModel):
    model_config = ConfigDict(extra="forbid",forzen=True)
    path: Path
    kind:FlatSourceKind
    app_id: str
    agent_id: str|None= None

class MigrationFileRecord(_MigrationModel):
    source_file: str
    backup_dir: str
    size: int
    mtime: int
    sha256:str
    backup_verified: str|None = None

class MigrationStatus(StrEnum):
    STARED = "stared"
    MIGRATED = "migrated"
    HISTORY_ONLY = "history_only"
    FAILED = "failed"

class MigrationManifest(_MigrationModel):
    schema_version: Literal[1]=1
    kind: MigrationKind
    status: MigrationStatus = MigrationStatus.STARED


