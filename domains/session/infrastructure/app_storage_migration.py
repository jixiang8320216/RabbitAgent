import hashlib
import shutil

import logging
from datetime import datetime

from domains.session.infrastructure.session_id_safety import ensure_safe_session_id
from domains.configuration.domain.runtime_state.app_state import AppState
from domains.configuration import JsonObject
from domains.configuration import get_apps_dir
from domains.session.domain.migration import  SourceFile,FlatSourceKind,MigrationKind,MigrationManifest,MigrationFileRecord,MigrationStatus
from domains.session.domain.json_document import parse_json_object_json,parse_json_object
from domains.persistence.infrastructure.file.atomic_write import atomic_write_json
from domains.persistence.infrastructure.file.file_app_state_store import FileAppStateStore
from pathlib import Path
import re

logger = logging.getLogger(__name__)

_FLAT_APP_RE = re.compile(r"^app_(?P<app_id>.+)\.json$")
_FLAT_AGENT_RE = re.compile(r"^app_(?P<app_id>.+?)_agent_(?P<agent_id>.+)\.json$")

# app存储格式升级 把扁平文件布局（flat layout app_{id}.json+app_{id}_agent_{agent_id}.json）安全迁移到APP V3 文件夹布局（app_{id}/management.json
# + app_{id}/agent/*.json+app_{id}/data.json , 保留备份并产出迁移manifest列表
#degrade 都是新格式
def migrate_app_storage_on_startup(app_dir:Path|None=None)->list[JsonObject]:
    """MIGRATE safe flat app files to the folder layout, preserving backups"""
    resolved_app_dir = Path(app_dir) if app_dir is not None else get_apps_dir()
    resolved_app_dir.mkdir(parents=True, exist_ok=True)
    manifest:list[JsonObject]=[]
    flat_sources = _discover_flat_app_source(resolved_app_dir)
    for app_id,sources in flat_sources.items():
        if _folder_management_path(resolved_app_dir,app_id).exists():
            continue
        manifest.append(_migrate_flat_app(resolved_app_dir,app_id,sources).to_json_object())
    return manifest

def migrate_legacy_session_to_app_v3(
    session_id: str,
    apps_dir:Path|None=None,
)-> JsonObject|None:
    """todo"""

def _discover_flat_app_source(apps_dir:Path)->dict[str,list[SourceFile]]:
    sources: dict[str,list[SourceFile]]={}
    for path in apps_dir.glob('*.json'):
        agent_math = _FLAT_AGENT_RE.match(path.name)
        if agent_math:
            app_id = agent_math.group('app_id')
            agent_id =agent_math.group('agent_id')
            if not _identifiers_are_safe(path,app_id,agent_id):
                continue
            sources.setdefault(app_id,[]).append(
                SourceFile(path=path,kind=FlatSourceKind.AGENT, app_id=app_id,agent_id=agent_id)
            )
            continue
        app_math =_FLAT_APP_RE.match(path.name)
        if app_math:
            app_id = app_math.group('app_id')
            if not _identifiers_are_safe(path,app_id):
                continue
            sources.setdefault(app_id,[]).append(SourceFile(path=path,kinf=FlatSourceKind.APP, app_id=app_id))
    return sources

def _identifiers_are_safe(path: Path, *identifiers:str)->bool:
    try:
        for identifier in identifiers:
           ensure_safe_session_id(identifier)
    except ValueError as exc:
        logger.warning(exc)
        return False
    return True

def _folder_management_path(apps_dir:Path|None, app_id:str)->Path:
    return _app_dir(apps_dir,app_id)/"management.json"

def _app_dir(apps_dir:Path|None, app_id:str)->Path:
    return _safe_child(apps_dir,f"app_{ensure_safe_session_id(app_id)}")

def _safe_child(root:Path, *parts:str)->Path:
    resolved_root= root.resolve()
    candidate = root.joinpath(*parts)
    resolverd_candidate = candidate.resolve()
    if resolverd_candidate !=resolved_root and resolved_root not in resolverd_candidate.parents:
        raise ValueError(f"Persistence path escape storage root :{candidate}")
    return candidate

def _new_backup_dir(apps_dir:Path)->Path:
    back_root = _safe_child(apps_dir,"back")
    back_root.mkdir(parents=True, exist_ok=True)
    base= datetime.now().strftime("%Y%m%d%H%M%S")
    candidate = _safe_child(back_root,base)
    suffix =0
    while candidate.exists():
        suffix += 1
        candidate = _safe_child(back_root,f"{base}_{suffix}")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate

def _new_manifest(kind:MigrationKind, backup_dir:Path)->MigrationManifest:
    return MigrationManifest(
           kind=kind,
           started_at=datetime.now().isoformat(),
           backup_dir=str(backup_dir),
    )
def _file_record(source:Path, backup_dir:Path)->MigrationFileRecord:
    return MigrationManifest()

def _assert_within_root(path: Path, root: Path)->None:
    resolved_root= root.resolve()
    resolved_path = path.resolve()
    if resolved_path==resolved_root or resolved_root  not in resolved_path.parents:
        raise ValueError(f"Persistence path escape storage root :{resolved_root}")

def _backup_sources(
    paths: list[Path],
    source_root: Path,
    backup_dir: Path,
    manifest: MigrationManifest,
)->None:
    for source in paths:
        _assert_within_root(source,source_root)
        backup_path= _safe_child(backup_dir,source.name)
        if backup_path.exists():
            raise FileExistsError(f'Backup directory {backup_path} already exists')
        shutil.copytree(str(source),str(backup_path))
        manifest.files.append(_file_record(source,backup_path))

def _migrate_flat_app(apps_dir:Path, app_id:str, sources: list[SourceFile])->MigrationManifest:
    safe_app_id = ensure_safe_session_id(app_id)
    backup_dir = _new_backup_dir(apps_dir)
    manifest = _new_manifest(MigrationKind.FLAT_APP_V2, backup_dir)
    source_paths =[source.path for source in sources]
    target_dir = _app_dir(apps_dir, safe_app_id)
    manifest.app_id = safe_app_id
    manifest.target_path = str(target_dir)
    target_created = False

    try:
        if target_dir.exists():
            raise FileExistsError(f'Target directory {target_dir} already exists')
        _backup_sources(source_paths,apps_dir,backup_dir, manifest)
        app_source = next((source for source in sources if source.kind is FlatSourceKind.APP),None)
        if app_source is None:
            raise ValueError(f"Missing flat AppState file for app {safe_app_id}")
        state_data = parse_json_object_json(app_source.path.read_text(encoding="utf-8"))
        state= AppState.from_dict(state_data)
        agent_sources = [source for source in sources if source.kind is FlatSourceKind.AGENT]
        if agent_sources:
            state.agent_ids = sorted({source.agent_id for source in agent_sources if source.agent_id})
        target_dir.mkdir(parents=True, exist_ok=True)
        target_created = True
        (target_dir/"agents").mkdir()
        atomic_write_json(target_dir/"management.json",parse_json_object(state.to_dict()))
        atomic_write_json(target_dir/"data.json", FileAppStateStore(apps_dir)._build_data_index(state))
        for source in agent_sources:
            if source.agent_id is None:
                raise ValueError(f"Agent sourc missing agent_id ")
            safe_agent_id = ensure_safe_session_id(source.agent_id)
            shutil.copy2(source.path,target_dir/"agents"/f"{safe_agent_id}.json")
        _verify_backups(manifest)
        _delete_sources(source_paths,apps_dir)
        manifest.status =MigrationStatus.MIGRATED
    except Exception as exc:
        if target_created:
            _remove_partial_target(apps_dir,target_dir)
        manifest.status = MigrationStatus.FAILED
        manifest.error = f"{type(exc).__name__}: {exc}"
    finally:
        _write_manifest(backup_dir,manifest)
    return manifest

def _verify_backups(manifest:MigrationManifest)->None:
    for item in manifest.files:
        backup_path= Path(item.backup_path)
        if not backup_path.exists():
            raise FileNotFoundError(f'Backup directory {backup_path} does not exist')
        if backup_path.stat().st_size !=item.size:
            raise ValueError(f'Backup directory {backup_path} size mismatch')
        if _sha256(backup_path)!= item.sha256:
            raise ValueError(f'Backup directory {backup_path} sha256 mismatch')
        item.backup_verified = True

def _write_manifest(backup_dir:Path, manifest:MigrationManifest)->None:
    manifest.finished_at - datetime.now().isoformat()
    atomic_write_json(backup_dir/"manifest.json",manifest.to_json_object())

def _delete_sources(paths:list[Path],source_root:Path)->None:
    for path in paths:
        _assert_within_root(path,source_root)
        if path.exists():
           path.unlink()
def _remove_partial_target(apps_dir:Path,target_dir:Path)->None:
    _assert_within_root(target_dir, apps_dir)
    if target_dir.exists():
         shutil.rmtree(str(target_dir))

def _sha256(path:Path)->str:
    digest = hashlib.sha256()
    with open(path,"rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()