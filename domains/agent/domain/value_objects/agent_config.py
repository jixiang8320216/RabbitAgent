"""Agent 构造期配置。

Agent 基类的可选构造参数。字段全部带默认值，未显式传入时以默认值兜底。

原先定义在 ``domains/configuration/config.py``（后随目录重组迁至
``infrastructure/logging_bootstrap.py``），但它并非「运行时配置」——
它只服务于 Agent 的构造，且仅被 agent 域内的 5 个 Agent 实现引用，
因此归位到 agent 域的值对象包。

命名由 ``Config`` 改为 ``AgentConfig``，避免与配置域的概念混淆。
"""

from typing import Any, Optional

from pydantic import BaseModel


class AgentConfig(BaseModel):
    """Agent 构造期配置。"""

    # LLM 配置
    default_model: str = "gpt-3.5-turbo"
    default_provider: str = "openai"
    temperature: float = 0.7
    max_tokens: Optional[int] = None

    # 系统配置
    debug: bool = False
    log_level: str = "INFO"

    # 其他配置
    max_history_length: int = 100

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """从环境变量创建配置。"""
        import os

        return cls(
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("MAX_TOKENS")) if os.getenv("MAX_TOKENS") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return self.model_dump()
