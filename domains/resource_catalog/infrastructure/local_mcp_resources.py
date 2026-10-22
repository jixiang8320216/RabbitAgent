from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class MCPServerScope(StrEnum):
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True)
class LocalMCPServer:
    name: str
    description: str = ""
    transport: str = "stdio"
    scope: MCPServerScope = MCPServerScope.USER
    active: bool = True
    timeout: int = 30
    sse_read_timeout: int = 30
    use_proxy: bool = False
    http_proxy: str | None = None
    https_proxy: str | None = None
    verify_ssl: bool = True
    protocol_version: str = "2025-06-18"
    capabilities: dict[str, Any] = field(default_factory=dict)
    skill_schema_validation: bool = True
    allow_missing_shema: bool = False
    strict_schema_validation: bool = False
    validate_parameters: bool = True
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cmd: str | None = None
    encoding: str = "utf-8"
    encoding_error_handler: str = "strict"


class LocalMCPRegistry:
    def __init__(self, servers: list[LocalMCPServer]) -> None:
        self._servers = tuple(servers)

    def list_all(self) -> tuple[LocalMCPServer, ...]:
        return self._servers


class LocalMCPDiscoveryService:
    """Discover MCP server definitions from system, user, and project config."""

    _CONFIG_NAMES = ("mcp_servers.json", "mcp.json", "config.json")

    def __init__(self, project_home: str | Path | None = None) -> None:
        self.project_home = Path(project_home or Path.cwd()).resolve(strict=False)

    def discover_all(self) -> LocalMCPRegistry:
        merged: dict[str, tuple[dict[str, Any], MCPServerScope]] = {}
        for path, scope in self._config_paths():
            for raw in self._read_servers(path):
                server = self._normalize(raw)
                if server is not None:
                    merged[server["name"]] = (server, scope)

        servers = [
            self._build_server(config, scope)
            for config, scope in merged.values()
        ]
        servers.sort(key=lambda server: server.name)
        return LocalMCPRegistry(servers)

    def _config_paths(self) -> list[tuple[Path, MCPServerScope]]:
        user_home = Path(os.environ.get("RABBIT_USER_HOME", Path.home() / ".rabbit"))
        # ``domains/resource_catalog/infrastructure/local_mcp_resources.py`` 的层级：
        # parents[0]=infrastructure, [1]=resource_catalog, [2]=domains, [3]=项目根。
        # 此前写 ``parents[2]`` 指向 ``<项目根>/domains/conf`` —— 目录永远不存在，
        # 系统级 MCP server 配置（SYSTEM scope）永远加载不到。
        system_dir = Path(__file__).resolve().parents[3] / "conf"
        roots = (
            (system_dir, MCPServerScope.SYSTEM),
            (user_home, MCPServerScope.USER),
            (self.project_home / ".rabbit", MCPServerScope.PROJECT),
        )
        return [
            (root / name, scope)
            for root, scope in roots
            for name in self._CONFIG_NAMES
            if (root / name).is_file()
        ]

    @staticmethod
    def _read_servers(path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        if isinstance(payload, dict):
            payload = payload.get("mcp_servers", payload.get("mcpServers", payload))
        if isinstance(payload, dict):
            payload = [dict(value, name=name) for name, value in payload.items() if isinstance(value, dict)]
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
        name = raw.get("name") or raw.get("id")
        if not isinstance(name, str) or not name.strip():
            return None
        normalized = dict(raw)
        normalized["name"] = name.strip()
        if "transport" not in normalized and "type" in normalized:
            normalized["transport"] = normalized["type"]
        if normalized.get("transport") == "http":
            normalized["transport"] = "streamable_http"
        return normalized

    @staticmethod
    def _build_server(config: dict[str, Any], scope: MCPServerScope) -> LocalMCPServer:
        allowed = {field.name for field in LocalMCPServer.__dataclass_fields__.values()}
        values = {key: value for key, value in config.items() if key in allowed}
        values["scope"] = scope
        values["args"] = [str(item) for item in values.get("args", [])]
        values["headers"] = dict(values.get("headers") or {})
        values["env"] = {str(key): str(value) for key, value in (values.get("env") or {}).items()}
        return LocalMCPServer(**values)