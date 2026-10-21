"""系统级资源容器。

``SystemResources`` 由 ``context/runtime_builder.py`` 的 ``_build_system_resources``
一次性装配，随后挂到 ``AppConfig.system_resources`` 上供应用装配层消费。

设计要点：

- 所有字段都带默认值。系统资源是「可缺省」的：任何一项（hook / skill /
  instruction / MCP）在磁盘上找不到时都不应阻断启动，因此这里不设置必填项。
- 大部分字段类型为 ``Any``，因为它们的真实生产者分散在 resource_catalog、
  skill_evolution、mcp 等包中，运行期以鸭子类型消费，硬绑类型反而会引入
  不必要的循环导入。
- 本模型保持 ``extra="forbid"``，让装配点写错字段名时立刻暴露，而不是被
  静默吞掉。
"""

from __future__ import annotations

import typing

import pydantic
import domains.hook.domain.inputs as hook_inputs


class MCPServerResource(pydantic.BaseModel):
    """MCP server 资源条目。

    ``context/runtime_builder.py`` 直接把 ``MCPServerConfig`` 实例放进
    ``mcp_servers`` 列表，因此本类只作为「未提供 config 对象时的等价占位」
    存在：字段与 MCP 配置的通用部分对齐，方便消费方按属性访问。
    """

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = ""
    description: str = ""
    command: str | None = None
    args: list[str] = pydantic.Field(default_factory=list)
    env: dict[str, str] = pydantic.Field(default_factory=dict)
    url: str | None = None
    enabled: bool = True

class InstructionResource(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")
    content:str
    file_path:str = pydantic.Field(min_length=1)
    memory_type:hook_inputs.InstructionMemoryType
    load_reason: hook_inputs.InstructionLoadReason = hook_inputs.InstructionLoadReason.SESSION_START
    globs: tuple[str, ...] |None=None
    trigger_file_path:str|None=None
    parent_file_path:str|None=None


class SystemResources(pydantic.BaseModel):
    """随包分发的系统资源集合。

    字段按职责分组：

    - skill：``skill_registry`` / ``skill_registry_refresher`` / ``rabbit_skills_md``
    - instruction：``instruction_resources``
    - MCP：``mcp_servers``
    - hook：``hooks_config`` 及配套的策略、来源、指纹、诊断与基础设施句柄
    """

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # --- skill ---
    skill_registry: typing.Any = None
    skill_registry_refresher: typing.Any = None
    rabbit_skills_md: str | None = None

    # --- instruction ---
    instruction_resources: typing.Any = None

    # --- MCP ---
    mcp_servers: list[typing.Any] = pydantic.Field(default_factory=list)

    # --- hook：合并后的配置快照与执行策略 ---
    hooks_config: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
    hook_policy: typing.Any = None

    # --- hook：配置来源追踪，用于诊断「这条 hook 从哪来」 ---
    hook_configuration_paths: tuple[typing.Any, ...] = ()
    hook_configuration_fingerprint: str | None = None
    hook_diagnostics: tuple[typing.Any, ...] | list[typing.Any] = pydantic.Field(default_factory=list)

    # --- hook：运行期句柄，由 composition 层注入 HookLifecycleCoordinator ---
    hook_configuration_refresher: typing.Any = None
    hook_component_context_updater: typing.Any = None
    hook_file_watcher: typing.Any = None
    hook_configuration_store: typing.Any = None

    @property
    def hook_config(self) -> dict[str, typing.Any]:
        """``hooks_config`` 的只读别名。

        生产方（``context/runtime_builder.py``）写入的字段名是 ``hooks_config``
        （复数），而消费方（``rabbit_application_composition.py``）读取的是
        ``hook_config``（单数）。两侧命名不一致，这里提供别名而不是改动任一
        调用点——生产方字段名与 ``SystemResources`` 其余 hook_* 字段的命名风格
        一致，而消费方的拼写在当前仓库只有一处。
        """
        return self.hooks_config
