"""根 agent（Plan Agent）配置模型。

``AgentConfigurationLoadingService._load_root`` 产出的就是本模型——它描述
「主 agent / 规划 agent」该怎么被构造：用什么模型、携带什么系统提示词、
工作在哪个应用模式下。

用 pydantic 模型而非裸 ``dataclass``：配置来自磁盘（模式声明 / 提示词文件），
字段量多且类型混杂（``pathlib.Path``、任意类型的 ``llm_config``），交给 pydantic
做校验可以在装配前就把「提示词是 None」「max_tokens 是负数」这类问题拦下来，
而不是等到构造 ``AgentRuntime`` 时才炸。
"""

from __future__ import annotations

import pathlib
import typing

import pydantic

__all__ = ["RootAgentConfig"]


class RootAgentConfig(pydantic.BaseModel):
    """根 agent 配置。

    :param name: agent 名称，默认 ``"planner"``。
    :param description: 一句话描述，用于 TUI/日志展示。
    :param system_prompt: 系统提示词正文（已合并项目提示词）。
    :param model: 模型名（新写法）。
    :param model_id: 显式指定的模型 ID（``root_agent_loader`` 仍在用这个名字）；
        ``None`` 表示沿用 ``llm_config``。两个名字都保留：改一处漏一处会让
        「配置里明明写了模型，agent 却用默认模型」这类问题极难排查。
    :param max_tokens: 单次生成上限；``0`` 表示沿用 ``llm_config``。
    :param llm_config: 宿主传入的默认 LLM 配置（通常来自 ``host.app.llm_config``）。
    :param mode: 应用模式（``delegate`` / ``roleplay`` / ``solo`` …）。
    :param prompt_path: 系统提示词的来源文件；``None`` 表示使用内置兜底提示词。
    :param source: 提示词来源标记，便于排查「为什么是这个提示词」。
    :param metadata: 附加信息（不参与 agent 构造）。
    :param mcp_servers: 允许挂载的 MCP 服务名；空表示不额外指定
        （仍受 ``AgentSlot.include_mcp_tools`` 开关约束）。
    :param allowed_tools: 工具白名单；``None`` 表示不限制。
    :param skill_refs: 技能引用；``None`` 表示不限制。
    :param strict_mcp_servers: 是否只允许显式列出的 MCP 服务。
    :param load_agents_md: 是否加载项目级 ``AGENTS.md``。

    ``__bool__`` 语义：只要实例存在即为真——上层用 ``if not plan_agent_config``
    判断的是「有没有加载出根配置」，而不是「配置内容是否完整」。
    """

    model_config = pydantic.ConfigDict(
        extra="forbid",
        validate_assignment=True,
        strict=True,
    )

    name: str = pydantic.Field( min_length=1)
    description: str = pydantic.Field( min_length=1)
    model: str =pydantic.Field( min_length=1)
    system_prompt: str = pydantic.Field( min_length=1)
    max_tokens: int = pydantic.Field(default=0, ge=0)

    # --- 能力约束（``mode_agent_creation._build_runtime_config`` 消费） ---
    mcp_servers: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] | None = None
    skill_refs: tuple[str, ...] | None = None
    strict_mcp_servers: bool = False
    load_agents_md: bool = True

    @property
    def has_prompt_file(self) -> bool:
        """系统提示词是否来自磁盘文件。

        用 ``getattr`` 取：字段集合还在调整，属性访问不该反过来成为崩点。
        """
        return getattr(self, "prompt_path", None) is not None

    @property
    def effective_model_id(self) -> str | None:
        """最终生效的模型标识。

        本类在 ``model_id`` 与 ``model`` 两个名字之间改过，取当前存在的那个，
        保证「生效的模型」永远只有一个答案。
        """
        return getattr(self, "model_id", None) or (self.model or None)

    def __repr__(self) -> str:
        return (
            f"RootAgentConfig(name={self.name!r}, "
            f"model={self.effective_model_id!r}, has_prompt_file={self.has_prompt_file!r})"
        )
