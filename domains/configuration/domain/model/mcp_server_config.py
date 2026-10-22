
from __future__ import annotations

from enum import StrEnum
from typing import Any
from pydantic import Field
from collections.abc import Mapping

from domains.configuration.domain.model.config_data import RuntimeConfigModel


class RuntimeMCPTransport(StrEnum):
    STDIO="stdio"
    SSE="sse"
    STREAMABLE_HTTP ="streamable_http"


class RuntimeMCPServerConfig(RuntimeConfigModel):
    name: str = ""
    description: str |None=None
    transport: RuntimeMCPTransport = RuntimeMCPTransport.STDIO
    scope: str = "system"
    active: bool = True
    url: str | None = None
    timeout: float = 30.0
    sse_read_timeout: float = 300.0
    use_proxy: bool = False
    http_proxy: str | None = None
    https_proxy: str | None = None
    verify_ssl: bool = False
    protocol_version: str = "2025-06-18"
    capabilities: dict[str, Any] = {}
    skill_schema_validation: bool = True
    allow_missing_shema: bool = False
    strict_schema_validation: bool = False
    validate_parameters: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cmd: str | None = None
    encoding: str = "utf-8"
    encoding_error_handler: str = "strict"

    @classmethod
    def from_raw(cls, value: object) -> RuntimeMCPServerConfig|None:
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.model_validate(value)
        return None

    @classmethod
    def list_from_raw(cls, value:object) -> list[RuntimeMCPServerConfig]:
        if not isinstance(value, list):
            return []
        servers: list[RuntimeMCPServerConfig] = []
        for item in value:
            server = cls.from_raw(item)
            if server is not None:
                servers.append(server)
        return servers
