import collections
import typing

import pydantic
import enum

from dashscope.acli.tools.registry import ToolDefinition

import domains.agent.domain.entities.agent_role as agent_role_module
import pathlib

from domains.agent.domain.aggregates.react_agent_streaming import ToolDeltaEmitter


class ImutablePluginModel(pydantic.BaseModel):

    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

@typing.runtime_checkable
class PluginEntryPoint(typing.Protocol):

    name:str
    value:str

    def load(self)->object:...

class PackageComponents(ImutablePluginModel):

    skills:tuple[str,...]=()
    mcp_servers: tuple[str,...]=()
    prompts: tuple[str,...]=()

class PluginMeta(ImutablePluginModel):
    name:str = pydantic.Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    path: pathlib.Path|None=None
    version: str = "0.0.0"
    description: str = ""
    doc:str=""
    author: str = ""
    dependencies: tuple[str,...]=()
    priority: int=0
    entry_point: PluginEntryPoint|None=None
    is_package:bool=False
    package_manifest:pathlib.Path|None=None
    package_components:PackageComponents |None=None
    package_scope: typing.Literal["project","user","system"]|None=None

class PluginInput(ImutablePluginModel):
    session_id:str
    project_name:pathlib.Path
    model_id:str=""
    provider_id:str=""
    config:dict[str, pydantic.JsonValue]= pydantic.Field(default_factory=dict)

class ToolGroupDefinition(ImutablePluginModel):
    name: str = pydantic.Field(min_length=1)
    description: str
    active:bool=False
    notes:str|None=None

class Hooks(ImutablePluginModel):
    tool:dict[str,ToolDefinition]=pydantic.Field(default_factory=dict)
    tool_groups: tuple[ToolGroupDefinition,...]=()

Plugin= collections.abc.Callable[[PluginInput], collections.abc.Awaitable[Hooks|None]]





class PluginState(enum.StrEnum):
    """插件的生命周期状态。

    状态流转::

        UNKNOWN -> LOADED -> DISABLED
                    |
                    +-> ERROR

    - ``UNKNOWN`` —— 已登记但尚未加载
    - ``LOADED`` —— 已加载并可参与贡献
    - ``DISABLED`` —— 被显式禁用或已关闭
    - ``ERROR`` —— 加载或初始化失败

    **历史缺口**：本枚举此前在迁移中丢失，而 ``PluginManager`` 一直引用它，
    导致插件管理路径一启动就抛 AttributeError。
    """

    UNKNOWN = "unknown"
    LOADED = "loaded"
    DISABLED = "disabled"
    ERROR = "error"


class ContributionType(enum.StrEnum):
    TOOL = "tool"
    SYSTEM_TRANSFRORM="system_transform"


class ContributionPolicy(ImutablePluginModel):
    contribution_type: ContributionType


class AgentSlot(ImutablePluginModel):
    """模式声明中的一个 agent 槽位。

    一个模式（``delegate`` / ``solo`` …）由若干 agent 槽位组成，其中被
    ``ModeDeclaration.root_agent`` 指名的那个就是根 agent（Plan Agent）。

    :param name: 槽位名，同时也是 agent 名。
    :param system_prompt_file: 系统提示词文件，相对 ``root_agent_prompts_dir``
        解析；为空表示该槽位不指定提示词文件（由加载器走兜底逻辑）。
    :param model: 槽位专属模型；为空表示沿用宿主默认 LLM 配置。
    :param description: 槽位说明。
    :param tools: 该槽位可用的工具名；空表示不额外限制。
    :param eager_creation: 是否在装配阶段就创建本槽位的 agent。``False`` 表示
        惰性创建（首次用到才建），适用于「声明了但本轮大概率用不到」的槽位。
        默认 ``True``：既兼容此前未声明该字段的既有声明，也符合「声明了就该有」
        的直觉——惰性是需要显式声明的优化，而不是默认行为。
    :param agent_type: 槽位的构建形态，决定由宿主的哪个构建方法负责。
        ``"orchestrator"`` 表示这是编排者槽位（要走
        ``AgentOrchestrator._build_orchestrator_agent``），其余取值交给通用构建
        路径。默认空字符串，即「未声明类型」。
    :param metadata: 附加信息。
    """

    name: str = pydantic.Field(min_length=1)
    agent_type: str="agent"
    system_prompt_file: str = ""
    agent_role: agent_role_module.AgentRole| None=None
    include_builtin_tools:bool=True
    include_mcp_tools:bool=True
    include_agent_tools:bool=True
    include_plan_runtime:bool=True
    include_plugin_tools:bool=True
    parallel_tool_calls:bool=True
    max_iters: int |None = pydantic.Field(default=None,ge=1)
    eager_creation: bool = True
    subagent_visibility:tuple[str,...]=()
    contribution_policies: dict[ContributionType, ContributionPolicy]=pydantic.Field(default_factory=dict)

    @pydantic.model_validator(mode="before")
    @classmethod
    def _accept_legacy_agent_tools_spelling(cls, value: object)->object:
        """兼容旧拼写 ``inclue_agent_tools``（少一个 ``d``）。

        字段已改回 ``include_agent_tools``（与 ``AgentRuntime`` 及所有消费点一致），
        但外部模式声明文件里可能还写着旧名。本类是 ``extra="forbid"``，直接改名字
        会让这些文件整体校验失败，所以在入口处做一次键名搬运。
        """
        if isinstance(value, dict) and "inclue_agent_tools" in value:
            value = dict(value)
            legacy = value.pop("inclue_agent_tools")
            value.setdefault("include_agent_tools", legacy)
        return value

#: 旧名兼容：本类此前叫 ``AgentSlotDeclaration``，多处调用点仍按旧名引用。
AgentSlotDeclaration = AgentSlot


class ModeDeclaration(ImutablePluginModel):
    """一个可注册的运行模式声明。

    :param name: 模式名，注册表的键；空值会被注册表拒绝。
    :param description: 面向用户的说明文本。
    :param kind: 模式类别，例如 ``"builtin"`` / ``"plugin"``。
    :param source: 声明来源标识（内置、某个插件名、某个 skill 名），
        用于诊断「这个模式是谁引入的」。
    :param root_agent: 根 agent 槽位名，指向 ``agent_slots`` 的某个键；
        为空表示本模式未声明根 agent（加载器走兜底逻辑）。
    :param agent_slots: 槽位名 -> 槽位声明。
    :param metadata: 附加信息，保持开放结构以容纳不同来源的扩展字段。
    """

    name: str = pydantic.Field(min_length=1)
    label: str = ""
    description: str = ""
    kind: str = ""
    source: str = ""
    # 为空表示本模式未声明根 agent（加载器走兜底逻辑），故不能约束 min_length
    root_agent: str = ""
    agent_slots: dict[str,AgentSlot] =pydantic.Field(default_factory=dict)
    default_contribution_policy: dict[ContributionType, ContributionPolicy] = pydantic.Field(default_factory=dict)
    tool_visibility_overrides: dict[str,dict[str,bool]] = pydantic.Field(default_factory=dict)
    context_strategy:str ="isolated"
    metadata: typing.Mapping[str, typing.Any] = pydantic.Field(default_factory=dict)

class TaskCompleteStatus(enum.StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

class TaskCompleteInput(ImutablePluginModel):
    session_id:str
    agent_name:str=""
    invocation_id:str=""
    project_home: pathlib.Path|None=None
    user_message:str=""
    final_response: str=""
    status: TaskCompleteStatus = TaskCompleteStatus.COMPLETED
    error:str=""
    metadata:dict[str, pydantic.JsonValue] = pydantic.Field(default_factory=dict)

class TaskCompleteOutput(ImutablePluginModel):
    metadata:dict[str, pydantic.JsonValue] = pydantic.Field(default_factory=dict)


ModeBuilder=collections.abc.Callable[...,collections.abc.Awaitable[object]|object]

class ModeDefinition(ImutablePluginModel):
    declaration:ModeDeclaration
    builder_override: ModeBuilder |None = None
























