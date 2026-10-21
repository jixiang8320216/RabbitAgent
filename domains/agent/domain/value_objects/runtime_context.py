
from __future__ import annotations

import pathlib

import pydantic


class RuntimeContext(pydantic.BaseModel):
    
    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed = True,
        extra = "forbid",
        validate_assignment=True,
        strict = True
    )

    user_data_home: pathlib.Path #用户数据根目录（会话、应用数据存放处）
    merged_config: dict[str,pydantic.JsonValue] = pydantic.Field(default_factory=dict) # 合并后的配置，且根据W3认证状态刷新了运行器投影（如账号相关字段）
    apps_dir: pathlib.Path |None = None  # App V3 持久化目录
    sessions_dir: pathlib.Path |None = None
    logs_dir: pathlib.Path |None = None  # 日志目录
    log_file: pathlib.Path |None = None  # 日志文件路径； port参数用于多实例区分文件名
    system_config_dir: pathlib.Path |None = None  # 系统级配置目录（harness运行时资源，如logging_cli.ini）
    bundled_mcp_node_bin_dir:pathlib.Path |None = None  # 内置MCP Node 二进制目录
    default_project_home: pathlib.Path |None = None # 进程启动时的工作目录

    @pydantic.field_validator(
        "user_data_home",
        "apps_dir",
        "sessions_dir",
        "logs_dir",
        "log_file",
        "system_config_dir",
        "bundled_mcp_node_bin_dir",
        "default_project_home",
        mode="before",
    )

    @classmethod
    def _normalize_path(cls, value:object) -> pathlib.path |None:
        if value is None or isinstance(value,pathlib.Path):
            return value
        if isinstance(value,str):
            return pathlib.Path(value)
        raise TypeError("RuntimeContext paths must be pathlib.Path, string, or None")

    @pydantic.model_validator(mode="after")
    def _synchronize_app_directory_alias(self)->RuntimeContext:
        if self.apps_dir is None and self.sessions_dir is not None:
            object.__setattr__(self,"apps_dir", self.sessions_dir)
        elif self.sessions_dir is None and self.apps_dir is not None:
            object.__setattr__(self,"sessions_dir", self.apps_dir)
        return self

_runtime_context: RuntimeContext | None=None

def get_runtime_context_or_none()->RuntimeContext|None:
    return _runtime_context

def get_runtime_context()->RuntimeContext | None:
    if _runtime_context is None:
        raise RuntimeError("RuntimeContext not set. Harness must install it before core runtime use")
    return _runtime_context

def get_user_data_home()->pathlib.Path:
    return get_runtime_context().user_data_home


def get_user_config_dir()->pathlib.Path:
    return get_user_data_home()

def get_user_config_file()->pathlib.Path:
    return get_user_config_dir()/"config.json"

def  set_runtime_context(context: RuntimeContext|None)->None:
     global _runtime_context
     _runtime_context = context


__all__=[
    "set_runtime_context",
    "get_user_config_file",
    "get_user_config_dir",
    "get_user_data_home",
    "get_runtime_context",
    "RuntimeContext",
    "get_runtime_context_or_none"
]

