from collections.abc import Mapping
from pydantic import Field

from domains.configuration.domain.model.config_data import RuntimeConfigModel
from domains.configuration.domain.model.config_data import ConfigMapping, ConfigData
from domains.configuration.domain.model.mcp_server_config import RuntimeMCPServerConfig


class RuntimeAppConfigOptions(RuntimeConfigModel):
    """"""


    prune_enabled: bool = Field(default=True, alias="pruneEnabled")
    prune_turns_to_protect: int = Field(default=2, alias="pruneTurnsToProtect")
    prune_tokens_to_project: int = Field(default=40_000, alias="pruneTokensToProject")
    prune_minimum_tokens: int = Field(default=1_000, alias="pruneMinimumTokens")
    prune_trigger_threshold: int = Field(default=100_000, alias="pruneTriggerThreshold")
    prune_protected_tools: list[str] = Field(default_factory=lambda: ["load_skill"],alias="pruneProtectTools")
    mcp_servers: list[RuntimeMCPServerConfig] | None = None
    microcompact_pct: float = Field(default=0.60, alias="microcompactPct")
    auto_compact_pct: float = Field(default=0.85, alias="autoCompactPct")
    keep_recent_tools: int =Field(default=10, alias="keepRecentTools")
    context_window_size: int | None = Field(default=None, alias="contextWindowSize")

    @classmethod
    def from_config(cls, config: ConfigMapping) -> RuntimeAppConfigOptions:
        return cls.model_validate(config)

    def mcp_server_config_data(self) -> list[ConfigData]:
        if self.mcp_servers is None:
            return []
        return [server.to_config_data() for server in self.mcp_servers]

    def context_window_size(
        self,
        *,
        merged_config: ConfigMapping | None = None,
        current_model_id: str | None = None,
        model_name: str | None = None,
        legacy_context_window_size: object = None,
    ) -> int | None:
        """解析上下文窗口大小（token 数）。

        优先级：显式传入的 legacy 值 > merged_config 中的
        contextWindowSize/context_window_size > 缺省 None。
        """
        if isinstance(legacy_context_window_size, int):
            return legacy_context_window_size
        if isinstance(merged_config, Mapping):
            for key in ("contextWindowSize", "context_window_size"):
                value = merged_config.get(key)
                if isinstance(value, int):
                    return value
        return None
