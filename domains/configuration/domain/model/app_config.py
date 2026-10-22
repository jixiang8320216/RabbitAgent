from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pydantic
from pydantic import Field

if TYPE_CHECKING:
    # 仅用于类型提示，运行期不导入，避免触发 memory/expert/mcp 等包的循环导入。
    # 原先这里写的是 ``from interfaces.acp.schema import ...``：定义本体早已下沉到
    # 本域的 ``runtime_descriptors``，``interfaces.acp.schema`` 只是一层 re-export。
    # 从领域层反向引用展示层会把依赖方向拧反（架构规则 R3），改为直接引用本体。
    from domains.configuration.domain.model.runtime_descriptors import (  # noqa: F401
        ModelInfo,
        SessionMode,
    )


class AppConfig(pydantic.BaseModel):
    """运行时核心配置：由 `build_runtime` 阶段 8 装配，供 CLI/ACP host 消费。"""

    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,  # 原 `forzen=True` 为拼写错误，已修正
        strict=True,
        validate_assignment=True,
    )

    llm_config: Any = None
    config_file: str = ""
    sub_agents_dir: str = ""
    root_agent_prompts_dir: str = ""
    user_project_home: str | None = None
    project_system_prompt: str | None = None
    app_mode: str = ""

    prune_enabled: bool = False
    prune_turns_to_protect: int = pydantic.Field(default=2, ge=0)
    prune_tokens_to_project: int =pydantic.Field(default=40_000, ge=0)
    prune_minimum_tokens: int =pydantic.Field(default=1_000, ge=0)
    prune_trigger_threshold: int = pydantic.Field(default=100_000, ge=0)
    prune_projected_tools: list[str]= pydantic.Field(default_factory=lambda :["load_skill"])

    disabled_plugins: list[str] = Field(default_factory=list)
    mcp_servers: list[Any] = Field(default_factory=list)
    system_resources: Any = None
    expert_configuration_source: Any = None
    runtime_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    memory_settings: Any = None

    microcompact_pct: float = 0.0
    auto_compact_pct: float = 0.0
    keep_recent_tools: int = pydantic.Field(default=10, ge=0)
    context_window_size: int = pydantic.Field(default=200_000, gt=0)
    config_overrides: dict[str, Any] | None = None
