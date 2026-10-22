"""guradrails 领域契约。

这里只放**授权判定**用到的值对象，不依赖任何应用层/基础设施层，保证领域层
可以被单独测试。

核心概念是 :class:`AuthorizationRule`（授权规则）：一条规则描述「对某一类操作
（``operation_type``），当目标（命令/URL/工具名…）匹配 ``pattern`` 时，应以
什么强度介入」。规则本身**不做决定**，只描述风险与介入方式，真正的判决由
应用层（``approval_workflow``）结合用户回复与插件策略做出。

三个维度各司其职，不要混用：

- :class:`RiskLevel`：这件事有多危险（决定确认话术的强度、是否记审计日志）。
- :class:`Scope`：一旦用户同意，同意的有效期（一次性 / 本会话 / 长期）。
- :class:`RuleAction`：默认动作——放行 / 询问 / 直接拒绝。

``operation_type`` 用普通 ``str`` 而不是枚举，是因为规则也来自 YAML/插件，
序列化形态就是字符串；:class:`OperationType` 只作为「内置类型」的常量表存在，
避免各处硬编码字面量拼错。
"""

from __future__ import annotations

import dataclasses
import enum
import re
import typing

__all__ = [
    "Scope",
    "RiskLevel",
    "RuleAction",
    "OperationType",
    "AuthorizationRule",
]

import pydantic


class Scope(str, enum.Enum):
    """授权同意的有效范围。

    ``ONCE`` 只放行当前这一次；``SESSION`` 在本次会话内不再追问；``ALWAYS``
    持久化到授权库，后续会话也不再追问；``NEVER`` 表示不接受同意（只能拒绝）。
    """

    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"
    NEVER = "never"


class RiskLevel(str, enum.Enum):
    """风险等级，决定确认话术强度与审计力度。

    顺序即严重程度，:meth:`RiskLevel.at_least` 依赖这个顺序做阈值比较。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def severity(self) -> int:
        """可用于比较的严重度序号。"""
        return _RISK_SEVERITY[self]

    def at_least(self, other: "RiskLevel") -> bool:
        """是否达到/超过 ``other`` 的风险等级。"""
        return self.severity >= other.severity


_RISK_SEVERITY: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class RuleAction(str, enum.Enum):
    """规则匹配后的默认动作。

    ``ALLOW`` 静默放行；``CONFIRM`` 交给人确认；``DENY`` 直接拒绝（不再询问，
    因为询问只是给一个不该做的操作提供"误点同意"的机会）。
    """

    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class OperationType(str, enum.Enum):
    """内置操作类型常量表。

    规则里仍以字符串书写（与 YAML/插件的形态一致），这里只作为防拼写错误的
    参照。 ``schedule_task`` / ``mcp`` / ``mcp_tool`` / ``network_request`` /
    ``git_push`` 属于「副作用外溢」类操作——影响超出当前工作区，因此默认值
    都比普通文件修改更严格。
    """

    CODE_MODIFICATION = "code_modification"
    SHELL_COMMAND = "shell_command"
    SCHEDULE_TASK = "schedule_task"
    MCP = "mcp"
    MCP_TOOL = "mcp_tool"
    NETWORK_REQUEST = "network_request"
    GIT_PUSH = "git_push"


class AuthorizationRule(pydantic.BaseModel):
    """一条授权规则。

    :param operation_type: 操作类型，如 ``shell_command`` / ``git_push``。
    :param pattern: 匹配目标的正则。对 shell 类操作目标是命令串；对
        ``network_request`` 通常是 ``"METHOD URL"``；对 ``mcp`` 是服务名或
        URL；对 ``mcp_tool`` 是工具名。用 :meth:`matches` 做匹配，不要自己
        ``re`` —— 这里的正则在构造时就编译并校验过了。
    :param scope: 同意的有效范围。
    :param risk_level: 风险等级。
    :param action: 默认动作；默认 ``CONFIRM``（问一句），最危险的操作才设
        ``DENY``。
    :param description: 给人看的一句话说明，会显示在确认提示里。
    :param enabled: 为假时规则不参与匹配（便于 YAML 侧临时停用而不删除）。
    """
    model_config= pydantic.ConfigDict(extra="forbid", frozen=True)

    operation_type: str = pydantic.Field(min_length=1)
    pattern: str
    scope: Scope = Scope.ONCE
    risk_level: RiskLevel = RiskLevel.MEDIUM
    description: str = ""
    action: RuleAction = RuleAction.CONFIRM
    expires_seconds:int = pydantic.Field(default=300, gt=0)
    auto_approve: bool = False
    enabled: bool = True


    #: 编译后的正则。构造时生成，不参与构造参数/比较/打印。
    compiled: re.Pattern[str] = dataclasses.field(
        init=False, repr=False, compare=False, default=None  # type: ignore[assignment]
    )

    def model_post_init(self, __context: object) -> None:
        """pydantic 的构造后钩子。

        注意钩子名：``pydantic.BaseModel`` 调的是 ``model_post_init``，
        **不会**调 dataclass 的 ``__post_init__``。写错名字不会报错，只会
        让 ``compiled`` 一直停在 ``None``，``matches()`` 全部抛
        ``AttributeError``——整个规则表静默失效，因此这里值得单独说明一句。

        模型 ``frozen=True``，赋值必须走 ``object.__setattr__``。
        """
        if isinstance(self.operation_type, OperationType):
            object.__setattr__(self, "operation_type", self.operation_type.value)
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(
                f"invalid pattern for {self.operation_type!r} rule: {self.pattern!r} ({exc})"
            ) from exc
        object.__setattr__(self, "compiled", compiled)

    def matches(self, operation_type: str, target: str) -> bool:
        """判断本规则是否命中给定操作。

        :param operation_type: 待判定的操作类型（字符串或 ``OperationType``）。
        :param target: 待匹配的目标串。
        :return: 命中返回真；规则被停用、类型不符或目标不是字符串时返回假。
        """
        if not self.enabled:
            return False
        if isinstance(operation_type, OperationType):
            operation_type = operation_type.value
        if self.operation_type != operation_type:
            return False
        # 非字符串目标一律不命中：``re.Pattern.search`` 遇 bytes/int 会抛 TypeError，
        # 而调用方可能来自未校验的外部输入。抛异常会让整条规则表求值中断，
        # 反而使后续规则（含高危规则）全部跳过——这正是防护被绕过的路径。
        # 这里选择「保守不匹配」，由调用方决定如何处理非法输入。
        if not isinstance(target, str):
            return False
        return self.compiled.search(target) is not None

    def __repr__(self) -> str:
        return (
            f"AuthorizationRule({self.operation_type}, "
            f"{self.risk_level.value}, {self.action.value}, {self.pattern!r})"
        )


#: 规则表的类型别名，供 ``policy.default_rules()`` 等处标注返回类型。
RuleTuple = typing.Tuple[AuthorizationRule, ...]
