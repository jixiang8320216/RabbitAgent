from pydantic import TypeAdapter,ValidationError
import copy
import json
import logging

from domains.persistence.infrastructure.file import atomic_write_text, FileLockTimeoutError, FileLockUnsafePathError
from domains.configuration import JsonObject,get_user_config_dir
from pathlib import Path
from typing import Never
from collections.abc import Callable
from domains.persistence.infrastructure.file.file_lock_impl import file_lock

logger = logging.getLogger(__name__)

USER_CONFIG_LOCK_TIMEOUT_SECONDS=5.0
_JSON_OBJECT_ADAPTER =TypeAdapter(JsonObject)


class UserConfigInvalidError(RuntimeError):
    """"""

class UserConfigBusError(RuntimeError):
    """"""

class UserConfigUnsafePathError(UserConfigInvalidError):
    """"""

def update_user_config[ResultT](
    mutate: Callable[[JsonObject],ResultT],
    *,
    config_path:str|Path|None=None,
    timeout_seconds: float=USER_CONFIG_LOCK_TIMEOUT_SECONDS,
)->ResultT:

    path = _safe_config_path(config_path)
    lock_path = _config_lock_path(path)
    try:
        with file_lock(lock_path, timeout=timeout_seconds):
            config = _read_config_file(path)
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
            atomic_write_text(path, content, encoding="utf-8")
            return result
    except FileLockTimeoutError as exc:
        raise UserConfigBusError(f"User Config is busy:{path}") from exc
    except FileLockUnsafePathError as exc:
        raise UserConfigUnsafePathError(f"User Config is busy:{path}") from exc


def read_user_config(config_path:str|Path|None= None)->JsonObject:
    path =_safe_config_path(config_path)
    return _read_config_file(path)


DEFAULT_LLM_CONFIG: JsonObject = {
    "llmConfigs": [
        {
            "provider": "openai",
            "model_name": "deepseek-flash",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
        }
    ]
}

def load_or_create_user_config() -> dict:
    """Load user config; create the file with defaults if missing or corrupt."""
    config_file: Path = get_user_config_dir() / "config.json"

    if config_file.exists():
        try:
            with config_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("Config %s has a non-object top level, falling back to defaults", config_file)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to parse config %s, falling back to defaults: %s", config_file, e)

    config_file.parent.mkdir(parents=True, exist_ok=True)
    with config_file.open("w", encoding="utf-8") as f:
        json.dump(DEFAULT_LLM_CONFIG, f, indent=2, ensure_ascii=False)
    logger.info("Created default config file %s", config_file)
    # Return a deep copy so callers cannot mutate the module-level default.
    return copy.deepcopy(DEFAULT_LLM_CONFIG)


def _safe_config_path(config_path: str|Path|None = None) -> Path:
    """解析用户配置的安全路径。

    不传参时用默认位置 ``<user config dir>/config.json``；传参时必须是**绝对路径**。

    安全约束：配置路径及其各级父目录都不能是符号链接或 reparse point。
    配置里存着 API key —— 允许符号链接就等于允许把 key 写到攻击者指定的
    位置（经典 symlink 攻击）。

    :raises UserConfigUnsafePathError: 路径不安全
    """
    if config_path is None:
        resolved = Path(get_user_config_dir()) / "config.json"
    else:
        candidate = Path(config_path)
        if not candidate.is_absolute():
            raise UserConfigUnsafePathError("config_path must be an absolute path")
        resolved = candidate

    # 逐级检查父目录 —— 只查文件本身不够，
    # 中间任一级目录是链接同样会把写入引到别处
    for parent in [resolved, *resolved.parents]:
        if _is_link_or_reparse_point(parent):
            raise UserConfigUnsafePathError(
                f"User config path must not be a symbolic link or reparse point: {parent}"
            )
    return resolved


def _config_lock_path(config_path:Path)->Path:
    return config_path.with_name(f".{config_path.name}.lock")

def _is_link_or_reparse_point(path:Path)->bool:
    return path.is_symlink() or path.is_junction()

def _reject_nonstandard_json_constant(value:str)->Never:
    raise ValueError(f"non-standard json value {value}")

def _read_config_file(config_path:Path)->JsonObject:
    if not config_path.exists():
        return {}
    try:
        parsed: object = json.loads(
            config_path.read_text(encoding="utf-8"),
            parse_constant = _reject_nonstandard_json_constant,
        )
    except (ValueError, UnicodeDecodeError, ValidationError) as e:
        raise UserConfigInvalidError(f"user config is not valid json {config_path}")
    if not isinstance(parsed, dict):
        raise UserConfigInvalidError(f"user config is not valid json {config_path}")
    return _JSON_OBJECT_ADAPTER.validate_python(parsed)