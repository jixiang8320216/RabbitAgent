"""内置模式声明。

把 ``configuration.infrastructure.merge.config_merger.DEFAULT_MODES``
（ACP 层的 ``SessionMode`` 元组，用于向客户端广告可用模式）适配为应用层的
``plugin.domain.ModeDeclaration``，供 ``ModelRegistry`` 注册。

为什么要做这层适配而不是让注册表直接存 ``SessionMode``：

- ``SessionMode`` 属于 ACP 协议层，字段由协议定义；``ModeDeclaration`` 属于
  应用领域，额外携带 ``source``（谁引入的）与开放的 ``metadata``。
- 注册表需要统一的 ``name`` 键与来源信息来做冲突诊断，协议对象不提供这些。

依赖方向：本模块导入 ACP 层是单向的（应用层 -> 协议层），不会造成反向耦合。
"""

from __future__ import annotations


import domains.plugin.domain as contracts
import domains.agent.domain.entities.agent_role as agent_role_module


DELEGATE_DECLARATION=contracts.ModeDeclaration(
    name="delegate",
    label="通用模式",
    description="直接处理任务， 并按普通技能和MCP的启用状态加载资源",
    root_agent="通用模式",
    agent_slots={
        "通用模式":contracts.AgentSlotDeclaration(
            name="通用模式",
            agent_type="general",
            system_prompt_file="RABBIT_GENERAL.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=True,
            include_plan_runtime=True,
            parallel_tool_calls=True,
            subagent_visibility=("*role","*utility"),
        ),
    },
    context_strategy="isolated",
)

SOLO_DECLARATION=contracts.ModeDeclaration(
    name="solo",
    label="单 Agent",
    description="由单一Agent 直接完成任务， 不启用任何Agent或专家委派入口",
    root_agent="单 Agent",
    agent_slots={
        "单 Agent":contracts.AgentSlotDeclaration(
            name="单 Agent",
            agent_type="solo",
            system_prompt_file="RABBITSOLO.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=True,
            include_agent_tools=False,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=(),
        ),
    },
    context_strategy="isolated",
)

ROLEPLAY_DECLARATION=contracts.ModeDeclaration(
    name="roleplay",
    label="角色扮演",
    description="选择一个Skill 或专家作为当前会话角色。",
    root_agent="角色扮演模式",
    agent_slots={
        "角色扮演模式":contracts.AgentSlotDeclaration(
            name="角色扮演模式",
            agent_type="general",
            system_prompt_file="RABBIT_GENERAL.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=True,
            include_plan_runtime=True,
            parallel_tool_calls=True,
            subagent_visibility=("*role","*utility"),
        ),
    },
    context_strategy="isolated",
)

GROUPCHAT_DECLARATION=contracts.ModeDeclaration(
    name="groupchat",
    label="群聊模式",
    description="由群聊便编排器组织多参与者讨论。",
    root_agent="群聊模式",
    agent_slots={
        "群聊模式":contracts.AgentSlotDeclaration(
            name="群聊模式",
            agent_type="orchestrator",
            system_prompt_file="RABBIT_ORCHESTRATOR.md",
            agent_role=agent_role_module.AgentRole.ORCHESTRATOR,
            include_builtin_tools=False,
            include_mcp_tools=False,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=("*role",),
        ),
    },
    context_strategy="share_board",
)




# ---------------------------------------------------------------------------
# 以下 4 个模式此前在 ``app.model.app_mode_configuration.BUILTIN_APP_MODES``
# 中已声明（并被 ``ModeAgentCreationService`` 列为「支持」），但**没有对应的
# ModeDeclaration 实现**——注册表 ``get_builtin_mode_definitions()`` 只有
# delegate / solo / roleplay / groupchat 四项。
#
# 后果：用户在 TUI 里选 guarded / pipeline / parallel / debate 时，
# ``ModelRegistry.get(mode)`` 返回 None，根 agent 只能走
# ``RootAgentLoader`` 的降级链（目录扫描 -> 内置兜底），
# 拿不到该模式专属的槽位拓扑与提示词，**四种模式实际都退化成默认 delegate 行为**，
# 且全程无任何报错。
#
# 这属于「声明支持但实际未实现」的静默降级，与 TUI 命令层的
# 「scaffold placeholder 谎报成功」是同一类问题。
# 本轮补齐声明，使「模式名 -> 拓扑」真正成立。
# ---------------------------------------------------------------------------

GUARDED_DECLARATION = contracts.ModeDeclaration(
    name="guarded",
    label="守护模式",
    description=(
        "带额外安全约束的执行模式：执行前先经规划槽位产出计划，"
        "经确认后再交给执行槽位落地，形成规划-执行分离的双层结构。"
    ),
    root_agent="守护规划",
    agent_slots={
        "守护规划": contracts.AgentSlotDeclaration(
            name="守护规划",
            agent_type="orchestrator",
            system_prompt_file="RABBIT_ORCHESTRATOR.md",
            agent_role=agent_role_module.AgentRole.ORCHESTRATOR,
            include_builtin_tools=False,
            include_mcp_tools=False,
            include_plan_runtime=True,
            parallel_tool_calls=False,
            subagent_visibility=("*role", "*utility"),
        ),
        "守护执行": contracts.AgentSlotDeclaration(
            name="守护执行",
            agent_type="general",
            system_prompt_file="RABBIT_GENERAL.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=True,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=("*role", "*utility"),
        ),
    },
    context_strategy="isolated",
)


PIPELINE_DECLARATION = contracts.ModeDeclaration(
    name="pipeline",
    label="流水线模式",
    description="固定顺序的多阶段处理：需求分析 -> 实现 -> 校验，逐阶段传递产物。",
    root_agent="流水线调度",
    agent_slots={
        "流水线调度": contracts.AgentSlotDeclaration(
            name="流水线调度",
            agent_type="orchestrator",
            system_prompt_file="RABBIT_ORCHESTRATOR.md",
            agent_role=agent_role_module.AgentRole.ORCHESTRATOR,
            include_builtin_tools=False,
            include_mcp_tools=False,
            include_plan_runtime=True,
            # 流水线强调阶段顺序，不并发
            parallel_tool_calls=False,
            subagent_visibility=("*role", "*utility"),
        ),
        "流水线执行": contracts.AgentSlotDeclaration(
            name="流水线执行",
            agent_type="general",
            system_prompt_file="RABBIT_GENERAL.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=True,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=("*role", "*utility"),
        ),
    },
    context_strategy="isolated",
)


PARALLEL_DECLARATION = contracts.ModeDeclaration(
    name="parallel",
    label="并行模式",
    description="同一目标分派给多个 agent 独立处理，最后汇总各方结论。",
    root_agent="并行调度",
    agent_slots={
        "并行调度": contracts.AgentSlotDeclaration(
            name="并行调度",
            agent_type="orchestrator",
            system_prompt_file="RABBIT_ORCHESTRATOR.md",
            agent_role=agent_role_module.AgentRole.ORCHESTRATOR,
            include_builtin_tools=False,
            include_mcp_tools=False,
            include_plan_runtime=False,
            # 并行模式的核心：调度侧并发扇出
            parallel_tool_calls=True,
            subagent_visibility=("*role", "*utility"),
        ),
        "并行执行": contracts.AgentSlotDeclaration(
            name="并行执行",
            agent_type="general",
            system_prompt_file="RABBIT_GENERAL.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=True,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=("*role", "*utility"),
        ),
    },
    context_strategy="isolated",
)


DEBATE_DECLARATION = contracts.ModeDeclaration(
    name="debate",
    label="辩证模式",
    description="对抗性多 agent 辩论：正反双方独立论证，再由裁判汇总裁决。",
    root_agent="辩论裁判",
    agent_slots={
        "辩论裁判": contracts.AgentSlotDeclaration(
            name="辩论裁判",
            agent_type="orchestrator",
            system_prompt_file="RABBIT_ORCHESTRATOR.md",
            agent_role=agent_role_module.AgentRole.ORCHESTRATOR,
            include_builtin_tools=False,
            include_mcp_tools=False,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=("*role", "*utility"),
        ),
        "正方": contracts.AgentSlotDeclaration(
            name="正方",
            agent_type="general",
            system_prompt_file="RABBIT_GENERAL.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=False,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=("*role",),
        ),
        "反方": contracts.AgentSlotDeclaration(
            name="反方",
            agent_type="general",
            system_prompt_file="RABBIT_GENERAL.md",
            agent_role=agent_role_module.AgentRole.GENERAL,
            include_builtin_tools=True,
            include_mcp_tools=False,
            include_plan_runtime=False,
            parallel_tool_calls=True,
            subagent_visibility=("*role",),
        ),
    },
    context_strategy="isolated",
)


def get_builtin_mode_definitions() -> dict[str, contracts.ModeDefinition]:
    """返回全部内置模式定义。

    此前只返回 delegate / solo / roleplay / groupchat 四项，
    而 ``BUILTIN_APP_MODES`` 声明了 8 种，导致 guarded / pipeline /
    parallel / debate 四种模式「声明支持但无定义」，静默退化成默认行为。
    """
    return {
        "delegate": contracts.ModeDefinition(declaration=DELEGATE_DECLARATION),
        "solo": contracts.ModeDefinition(declaration=SOLO_DECLARATION),
        "roleplay": contracts.ModeDefinition(declaration=ROLEPLAY_DECLARATION),
        "groupchat": contracts.ModeDefinition(declaration=GROUPCHAT_DECLARATION),
        "guarded": contracts.ModeDefinition(declaration=GUARDED_DECLARATION),
        "pipeline": contracts.ModeDefinition(declaration=PIPELINE_DECLARATION),
        "parallel": contracts.ModeDefinition(declaration=PARALLEL_DECLARATION),
        "debate": contracts.ModeDefinition(declaration=DEBATE_DECLARATION),
    }
