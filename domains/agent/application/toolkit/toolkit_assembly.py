"""Agent 工具集装配结果。

``AgentToolkitBuilder.prepare_toolkit_assembly`` 的返回类型。装配过程跨多个
工具来源（内置 / MCP / 技能 / 委派 agent），每个来源的成熟度不一样，因此结果
里除了最终的 ``toolkit``，还保留**每个工具是从哪来的**——下游（提示词、权限、
诊断）按来源分流时要用。

约定：任何来源缺席都只是该来源的工具不在装配结果里，``toolkit`` 可以为
``None``，但装配结果本身总是能构造出来。
"""

from __future__ import annotations

import dataclasses
import enum
import typing

__all__ = [
    "ToolSource",
    "ToolClassification",
    "ToolClassifier",
    "AgentToolkitAssembly",
]


class ToolSource(enum.StrEnum):
    """工具来源。"""

    BUILTIN = "builtin"
    MCP = "mcp"
    SKILL = "skill"
    AGENT = "agent"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True, slots=True)
class ToolClassification:
    """单个工具的归类结果。

    :param name: 工具名。MCP 来源在工具级清单可用前先记服务名。
    :param source: 来源类别。
    :param origin: 更细的来源标识，例如 MCP 服务名。
    """

    name: str
    source: ToolSource = ToolSource.UNKNOWN
    origin: str = ""


class ToolClassifier:
    """工具名 -> 来源 的解析器。

    查不到的名字返回 :attr:`ToolSource.UNKNOWN` 而不是抛异常——未知工具不该让
    调用方崩掉。
    """

    __slots__ = ("_by_name",)

    def __init__(self, classifications: typing.Iterable[ToolClassification] = ()) -> None:
        self._by_name: dict[str, ToolClassification] = {
            item.name: item for item in classifications if item.name
        }

    def __call__(self, tool_name: str) -> ToolSource:
        """按工具名取来源；未登记的名字返回 ``UNKNOWN``。"""
        if not isinstance(tool_name, str):
            return ToolSource.UNKNOWN
        found = self._by_name.get(tool_name)
        return found.source if found is not None else ToolSource.UNKNOWN

    def classification_of(self, tool_name: str) -> ToolClassification | None:
        """取完整归类结果；未登记返回 ``None``。"""
        if not isinstance(tool_name, str):
            return None
        return self._by_name.get(tool_name)

    def names_of(self, source: ToolSource | str) -> tuple[str, ...]:
        """某一来源下的全部工具名。"""
        wanted = ToolSource(source)
        return tuple(
            name for name, item in self._by_name.items() if item.source is wanted
        )

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, tool_name: object) -> bool:
        return isinstance(tool_name, str) and tool_name in self._by_name

    def __repr__(self) -> str:
        return f"ToolClassifier({len(self._by_name)} tool(s))"


@dataclasses.dataclass(slots=True)
class AgentToolkitAssembly:
    """一次工具集装配的产物。

    :param toolkit: 组装完成的工具集；``Toolkit`` 尚未实现时为 ``None``。
    :param tool_classification_resolver: 工具名 -> 来源 的解析器。
    :param classifications: 全量归类结果，便于诊断与序列化。
    :param tool_names: 全部工具名（各来源按 内置→MCP→技能→agent 的顺序拼接）。
    :param mcp_server_names: 本次纳入的 MCP 服务名。
    :param skill_names: 本次纳入的技能名。
    :param warnings: 装配过程中的降级说明；为空表示没有降级。
    """

    toolkit: typing.Any | None = None
    tool_classification_resolver: ToolClassifier | None = None
    classifications: tuple[ToolClassification, ...] = ()
    tool_names: tuple[str, ...] = ()
    mcp_server_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def has_toolkit(self) -> bool:
        """是否拿到了可用的工具集对象。"""
        return self.toolkit is not None

    def names_of(self, source: ToolSource | str) -> tuple[str, ...]:
        """某一来源下的工具名；无解析器时回退到全量名字里筛。"""
        if self.tool_classification_resolver is not None:
            return self.tool_classification_resolver.names_of(source)
        wanted = ToolSource(source)
        return tuple(
            item.name for item in self.classifications if item.source is wanted
        )

    def __repr__(self) -> str:
        return (
            f"AgentToolkitAssembly(toolkit={'yes' if self.has_toolkit else 'None'}, "
            f"tools={len(self.tool_names)}, mcp={len(self.mcp_server_names)}, "
            f"skills={len(self.skill_names)}, warnings={len(self.warnings)})"
        )
