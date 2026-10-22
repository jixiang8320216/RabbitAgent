"""插件契约的集中再导出（应用层门面）。

迁移把多个源顶层包折叠进 ``domains.plugin`` 后，历史调用点通过
``domains.plugin.application.plugin_contracts`` 访问一批契约符号
（``AgentSlot`` / ``ModeDeclaration`` / 各类变换 I/O 等）。本模块把这些符号
统一从这里导出，避免散落在调用点的相对导入在迁移后失效。

领域层真正的定义在 ``domains.plugin.domain.contribution``；本模块优先从那里
再导出，缺失的 I/O 契约按最小可用形态在此补齐（``extra="ignore"``，便于与
具体插件实现逐步对齐，且不影响导入期完整性）。
"""

from __future__ import annotations

import enum

import pydantic

import domains.plugin.domain.contribution as contribution


# ── 来自领域层 contribution 的既有契约 ─────────────────────────
AgentSlot = contribution.AgentSlot
ModeDeclaration = contribution.ModeDeclaration
TaskCompleteInput = contribution.TaskCompleteInput
TaskCompleteOutput = contribution.TaskCompleteOutput
TaskCompleteStatus = contribution.TaskCompleteStatus
ToolDefinition = contribution.ToolDefinition
PluginEntryPoint = contribution.PluginEntryPoint


class _PluginIO(pydantic.BaseModel):
    """插件变换 I/O 的最小基类：宽松接受任意字段，便于迁移期对齐。"""

    model_config = pydantic.ConfigDict(extra="ignore")


class ChatHeaderInput(_PluginIO):
    headers: dict[str, str] = pydantic.Field(default_factory=dict)


class ChatHeaderOutput(_PluginIO):
    """系统提示变换的输出（最小可用形态）。"""


class SystemTransformInput(_PluginIO):
    """系统提示变换的输入（最小可用形态）。"""


class SystemTransformOutput(_PluginIO):
    """系统提示变换的输出（最小可用形态）。"""


class SessionLifecycelInput(_PluginIO):
    """会话生命周期变换的输入（注意：调用点沿用了 ``Lifecycel`` 拼写）。"""


class SessionLifecycleOutput(_PluginIO):
    """会话生命周期变换的输出。"""


class TaskStatus(enum.StrEnum):
    """任务执行状态枚举（最小可用成员集）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


__all__ = [
    "AgentSlot",
    "ModeDeclaration",
    "TaskCompleteInput",
    "TaskCompleteOutput",
    "TaskCompleteStatus",
    "ToolDefinition",
    "PluginEntryPoint",
    "ChatHeaderInput",
    "ChatHeaderOutput",
    "SystemTransformInput",
    "SystemTransformOutput",
    "SessionLifecycelInput",
    "SessionLifecycleOutput",
    "TaskStatus",
]
