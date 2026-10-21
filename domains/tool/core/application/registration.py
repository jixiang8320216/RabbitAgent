"""工具注册服务。

``AgentToolkitBuilder`` 在装配工具集时会建一个 ``ToolRegistryService``，用它做
两件事：

1. **登记每个工具的来源**（内置 / MCP / 技能 / 委派 agent）。下游（提示词、
   权限、诊断）按来源分流，``AgentToolkitAssembly.names_of`` 直接转发到
   这里的 :meth:`names_of`。
2. **作为 ``tool_classification_resolver``** 传给 ``ReActAgent``，让运行侧能
   现场问「这个工具是哪来的」。
"""

from __future__ import annotations

import enum
import typing

import domains.tool.core.domain.contracts as metadata_module
__all__ = ["ToolSource", "ToolRegistryService"]


class ToolSource(enum.StrEnum):
    """工具来源（与 ``agent.service.toolkit_assembly.ToolSource`` 同值）。"""

    BUILTIN = "builtin"
    MCP = "mcp"
    SKILL = "skill"
    AGENT = "agent"
    UNKNOWN = "unknown"


def _coerce_source(source: object) -> ToolSource:
    """把任意入参转成 ``ToolSource``；转不了就 ``UNKNOWN``（不抛）。"""
    if isinstance(source, ToolSource):
        return source
    if isinstance(source, str):
        try:
            return ToolSource(source.strip().lower())
        except ValueError:
            return ToolSource.UNKNOWN
    return ToolSource.UNKNOWN


class ToolRegistryService:
    """工具名 -> 来源 的登记表。"""

    def __init__(self, app_instance: object|None = None) -> None:
        self.app_instance = app_instance
        # ``ToolCatalog`` 目前还是空壳（``tool.core.domain.contracts`` 只声明了
        # 名字），真正的数据先落在下面两张表里；等 catalog 落地再迁过去，
        # 调用方无需改动。
        catalog_cls = getattr(metadata_module, "ToolCatalog", None)
        self.registry: typing.Any = catalog_cls() if catalog_cls is not None else None
        self._by_name: dict[str, ToolSource] = {}
        self._origin_by_name: dict[str, str] = {}

    # --- 登记 ----------------------------------------------------------
    def register_tool(
        self,
        tool_name: str,
        source: object = ToolSource.UNKNOWN,
        origin: str = "",
    ) -> None:
        """登记一个工具的来源。

        装配流水线会跑多轮（内置 → MCP → 技能 → agent），同一个工具名可能被
        多个来源声明。这里采取「已知来源优先」：后到的 ``UNKNOWN`` 不覆盖已知
        来源——否则一次「来源未知」的补登记就会把正确来源洗掉。
        """
        if not isinstance(tool_name, str) or not tool_name:
            return
        incoming = _coerce_source(source)
        known = self._by_name.get(tool_name)
        if known is None or (known is ToolSource.UNKNOWN and incoming is not ToolSource.UNKNOWN):
            self._by_name[tool_name] = incoming
        if origin and not self._origin_by_name.get(tool_name):
            self._origin_by_name[tool_name] = origin

    def register_builtin_tool(self, tool_name: str, origin: str = "") -> None:
        self.register_tool(tool_name, ToolSource.BUILTIN, origin)

    def register_mcp_tool(self, tool_name: str, origin: str = "") -> None:
        self.register_tool(tool_name, ToolSource.MCP, origin)

    def register_skill_tool(self, tool_name: str, origin: str = "") -> None:
        self.register_tool(tool_name, ToolSource.SKILL, origin)

    def register_subagent_tool(self, tool_name: str, origin: str = "") -> None:
        self.register_tool(tool_name, ToolSource.AGENT, origin)

    def unregister_tool(self, tool_name: str) -> None:
        """反注册（与 ``Toolkit.unregister_tool`` 语义一致：不存在也不报错）。"""
        self._by_name.pop(tool_name, None)
        self._origin_by_name.pop(tool_name, None)

    # --- 查询 ----------------------------------------------------------
    def source_of(self, tool_name: str) -> ToolSource:
        """工具来源；未登记返回 ``UNKNOWN``（不抛）。"""
        if not isinstance(tool_name, str):
            return ToolSource.UNKNOWN
        return self._by_name.get(tool_name, ToolSource.UNKNOWN)

    # ``ReActAgent`` / ``AgentToolkitAssembly`` 都可能按调用形式取用。
    __call__ = source_of

    def origin_of(self, tool_name: str) -> str:
        """更细的来源标识（例如 MCP 服务名）；没有则空串。"""
        return self._origin_by_name.get(tool_name, "")

    def names_of(self, source: object) -> tuple[str, ...]:
        """某一来源下的全部工具名（保持登记顺序）。"""
        wanted = _coerce_source(source)
        return tuple(name for name, found in self._by_name.items() if found is wanted)

    def classifications(self) -> tuple[tuple[str, ToolSource, str], ...]:
        """全量 ``(工具名, 来源, origin)``，便于诊断与序列化。"""
        return tuple(
            (name, found, self._origin_by_name.get(name, ""))
            for name, found in self._by_name.items()
        )

    def __contains__(self, tool_name: object) -> bool:
        return isinstance(tool_name, str) and tool_name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)

    def __iter__(self) -> typing.Iterator[str]:
        return iter(self._by_name)

    def __repr__(self) -> str:
        return f"ToolRegistryService({len(self._by_name)} tool(s))"
