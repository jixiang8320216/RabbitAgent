# -*- coding: utf-8 -*-
"""L1-1 MCP：工具发现、参数构造、结果解析、失败处理、超时降级、并发安全。

MCP 客户端的真实连接需要 fastmcp 与外部 server（指南 L6：需外部服务），
因此本文件聚焦**可离线验证的链路**：投影模式、服务注册表、离线发现、
工具登记到 Toolkit 的完整装配路径。
"""

from __future__ import annotations

import __future__
import asyncio
import json

import pytest

from framework.case import case, require, require_eq


# ── L1-1.1 工具发现 ─────────────────────────────────────────────
@case(
    "L1-1.1",
    "MCP 投影模式可解析：命中网关插件名返回 GATEWAY，否则返回 EAGER",
    title="MCP 投影模式解析错误",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="配置了网关插件仍返回 EAGER → 工具重复注册",
    tags=("黄金",),
)
@pytest.mark.parametrize(
    "plugins,expected",
    [
        ([], "eager"),
        (None, "eager"),
        (["mcp_tool_gateway"], "gateway"),
        (["other"], "eager"),
        (["a", "mcp_tool_gateway"], "gateway"),
    ],
    ids=["空列表→EAGER", "None→EAGER", "网关→GATEWAY", "无关插件→EAGER", "含网关→GATEWAY"],
)
def test_projection_mode_resolution(plugins, expected):
    from domains.mcp.domain.tool_projection import resolve_projection_mode

    mode = resolve_projection_mode(plugins)
    require_eq(mode.value, expected, f"插件 {plugins} 的投影模式")


@case(
    "L1-1.1",
    "自定义网关插件名参数生效",
    title="自定义网关插件名未生效",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="传入自定义网关名仍走默认名匹配 → 投影模式错误",
    tags=("边界",),
)
def test_custom_gateway_name():
    from domains.mcp.domain.tool_projection import resolve_projection_mode

    mode = resolve_projection_mode(["custom_gw"], gateway_plugin_name="custom_gw")
    require_eq(mode.value, "gateway", "自定义网关名的投影模式")


@case(
    "L1-1.1",
    "MCP 服务注册表支持按名查找、列举与包含判断",
    title="服务注册表查询能力缺失",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="无法按名查找服务 → 工具无法归属到服务",
    tags=("黄金",),
)
def test_server_registry_lookup():
    from domains.mcp.infrastructure.client_runtime import MCPServerRegistry

    registry = MCPServerRegistry([{"name": "srv-a"}, {"name": "srv-b"}])
    require_eq(registry.names(), ("srv-a", "srv-b"), "服务名列表")
    require_eq(len(registry), 2, "服务数量")
    require("srv-a" in registry, "包含判断")
    require(registry.get("missing") is None, "不存在的服务应返回 None")


@case(
    "L1-1.1",
    "离线 MCP 服务发现：能从项目配置中解析 stdio / http / sse 三类传输",
    title="离线 MCP 服务发现不可用",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="无法读取本地 mcp_servers.json → 已配置的服务不可见",
    tags=("黄金",),
)
def test_offline_mcp_discovery(tmp_path):
    from domains.resource_catalog.infrastructure.local_mcp_resources import (
        LocalMCPDiscoveryService,
        MCPServerScope,
    )

    config_dir = tmp_path / ".rabbit"
    config_dir.mkdir()
    (config_dir / "mcp_servers.json").write_text(
        json.dumps([
            {"name": "local-fs", "transport": "stdio", "command": "npx", "args": ["-y", "fs"]},
            {"name": "http-srv", "transport": "streamable_http", "url": "https://example.com/mcp"},
            {"name": "sse-srv", "type": "sse", "url": "https://example.com/sse"},
        ]),
        encoding="utf-8",
    )

    servers = LocalMCPDiscoveryService(project_home=tmp_path).discover_all().list_all()
    by_name = {s.name: s for s in servers}

    require_eq(sorted(by_name), ["http-srv", "local-fs", "sse-srv"], "发现的服务名")
    require_eq(by_name["local-fs"].transport, "stdio", "local-fs 传输类型")
    require_eq(by_name["local-fs"].command, "npx", "local-fs 启动命令")
    require_eq(by_name["local-fs"].args, ["-y", "fs"], "local-fs 参数")
    require_eq(by_name["http-srv"].transport, "streamable_http", "http-srv 传输类型")
    require_eq(by_name["sse-srv"].transport, "sse", "sse-srv 传输类型（type 字段归一）")
    require_eq(by_name["local-fs"].scope, MCPServerScope.PROJECT, "local-fs 作用域")


@case(
    "L1-1.1",
    "MCPManager 装配后其注册表包含传入的服务配置",
    title="MCPManager 忽略传入的服务配置",
    dimension="MCP - 工具发现",
    severity="P0",
    threshold="100%",
    counter_example="传入 runtime_configs 但 len(manager)==0 → 所有 MCP 服务不可用",
    tags=("黄金",),
)
def test_manager_registers_configs():
    from domains.mcp.infrastructure.client_runtime import MCPManager

    manager = MCPManager(runtime_configs=[{"name": "srv-x", "transport": "stdio"}])
    require(
        len(manager) > 0,
        "MCPManager 忽略了传入的 runtime_configs：注册表恒为空，所有已配置服务不可用",
        detail=f"len(manager)={len(manager)}, server_configs={manager.server_configs}",
    )
    require("srv-x" in manager.mcp_registry.names(), "已配置服务未进入注册表")


@case(
    "L1-1.1",
    "停用的 MCP 服务（active=False）不进入运行时注册表",
    title="停用的 MCP 服务仍被装配",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="active=False 的服务仍被装配 → 停用开关无效",
    tags=("边界",),
)
def test_inactive_servers_excluded():
    from domains.mcp.infrastructure.client_runtime import MCPManager

    manager = MCPManager(
        runtime_configs=[{"name": "off", "active": False}, {"name": "on", "active": True}]
    )
    names = manager.mcp_registry.names()
    # 前置条件：注册表**非空**（启用的 "on" 必须在表里），否则过滤行为无从观察。
    # 注意不能写成 `"off" in names`——那与下一条断言（"off" 不得入表）直接矛盾，
    # 任何实现都不可能同时满足。
    require(
        "on" in names,
        "注册表恒为空，无法验证停用过滤（见 test_manager_registers_configs）",
    )
    require("off" not in names, "停用服务被装配进运行时", detail=f"注册表：{names}")


# ── L1-1.2 参数构造 ─────────────────────────────────────────────
_ANALYZE_SRC = (
    "def analyze(path: str, rows: int = 10, verbose: bool = False, "
    "tags: list = None) -> str:\n"
    '    """分析数据文件"""\n'
    "    return path\n"
)


def _build_analyze(*, postponed_annotations: bool):
    """构造 analyze 函数，可选择是否启用 postponed annotations。

    必须用 compile(..., dont_inherit=True) 显式切断编译标志继承：
    exec() 会继承调用方模块的 __future__ 标志，导致无法构造出
    「注解为真实类型对象」这一基线场景。
    """
    flags = __future__.annotations.compiler_flag if postponed_annotations else 0
    code = compile(_ANALYZE_SRC, "<probe>", "exec", flags=flags, dont_inherit=True)
    namespace: dict = {}
    exec(code, namespace)
    return namespace["analyze"]


@case(
    "L1-1.2",
    "工具注册时从函数签名自动生成 JSON Schema，必填参数正确标记",
    title="工具参数 Schema 构造错误",
    dimension="MCP - 参数构造",
    severity="P1",
    threshold="≥ 99%",
    counter_example="必填参数未被标记为 required → 模型调用时漏参",
    tags=("黄金",),
)
def test_tool_schema_generation():
    """基线能力：注解为真实类型对象时，类型推断应正确。"""
    from domains.tool.core.application.toolkit import Toolkit

    analyze = _build_analyze(postponed_annotations=False)
    require(
        analyze.__annotations__["verbose"] is bool,
        "未构造出真实类型注解，测试前提不成立",
        detail=f"实际注解：{analyze.__annotations__}",
    )

    reg = Toolkit().register_tool_function(analyze, name="analyze")
    schema = reg.json_schema()

    require_eq(schema["type"], "function", "schema 顶层类型")
    require_eq(schema["function"]["name"], "analyze", "schema 中的工具名")
    require_eq(schema["function"]["description"], "分析数据文件", "schema 描述（取自 docstring）")
    require_eq(
        sorted(schema["function"]["parameters"]["required"]), ["path"], "必填参数列表"
    )
    props = schema["function"]["parameters"]["properties"]
    require_eq(props["path"]["type"], "string", "path 参数类型")
    require_eq(props["verbose"]["type"], "boolean", "verbose 参数类型推断")
    require_eq(props["tags"]["type"], "array", "tags 参数类型推断")
    # 数值类型统一映射为 JSON Schema 的 number（兼容各 provider，不细分为 integer）
    require_eq(props["rows"]["type"], "number", "rows 参数类型推断")


@case(
    "L1-1.2",
    "启用 from __future__ import annotations 时，参数类型推断仍正确",
    title="注解字符串化导致工具参数类型全部退化为 string",
    dimension="MCP - 参数构造",
    severity="P0",
    threshold="100%",
    counter_example="所有参数 type 均为 string → 模型收到错误 schema，"
    "整数参数被当字符串传入，工具调用在运行期失败",
    tags=("故障", "P0"),
)
def test_schema_types_survive_string_annotations():
    """Python ≥3.10 项目普遍使用 from __future__ import annotations。

    此时函数注解在定义时即为字符串（如 'int'、'bool'、'list[str]'），
    而 toolkit._json_type 用 `annotation is int` 这类身份比较，
    对字符串恒不成立 → 一律回落为 "string"。
    """
    from domains.tool.core.application.toolkit import Toolkit

    analyze = _build_analyze(postponed_annotations=True)
    require(
        isinstance(analyze.__annotations__["verbose"], str),
        "未复现注解字符串化，测试前提不成立",
        detail=f"实际注解：{analyze.__annotations__}",
    )

    reg = Toolkit().register_tool_function(analyze, name="analyze")
    props = reg.json_schema()["function"]["parameters"]["properties"]

    inferred = {k: v.get("type") for k, v in props.items()}
    degraded = sorted(k for k, t in inferred.items() if t == "string")
    require(
        "verbose" not in degraded and "rows" not in degraded and "tags" not in degraded,
        f"参数 {degraded} 的类型被推断为 string："
        "toolkit._json_type 用 `annotation is int` 身份比较，"
        "对 from __future__ import annotations 产生的字符串注解恒不成立，"
        "全部回落为 string → 模型收到错误 schema，整数/布尔/数组参数被当字符串传入",
        detail=f"实际推断结果：{inferred}",
    )
    require_eq(props["rows"]["type"], "number", "rows 参数类型（注解为字符串 'int'）")
    require_eq(props["verbose"]["type"], "boolean", "verbose 参数类型（注解为字符串 'bool'）")
    require_eq(props["tags"]["type"], "array", "tags 参数类型（注解为字符串 'list'）")


@case(
    "L1-1.2",
    "同名工具注册策略生效：skip 返回 None、raise 抛错、rename 生成后缀",
    title="同名工具注册策略失效",
    dimension="MCP - 参数构造",
    severity="P1",
    threshold="100%",
    counter_example="skip 策略下同名工具被覆盖 → 模型调用到错误的实现",
    tags=("边界",),
)
def test_namesake_strategies():
    from domains.tool.core.application.toolkit import (
        NAMESAKE_RAISE,
        NAMESAKE_RENAME,
        NAMESAKE_SKIP,
        Toolkit,
    )

    toolkit = Toolkit()

    def f(x: int = 1) -> int:
        return x

    require(toolkit.register_tool_function(f, name="t") is not None, "首次注册失败")
    require(
        toolkit.register_tool_function(f, name="t", namesake_strategy=NAMESAKE_SKIP) is None,
        "skip 策略下同名注册未返回 None",
    )
    with pytest.raises(ValueError):
        toolkit.register_tool_function(f, name="t", namesake_strategy=NAMESAKE_RAISE)
    renamed = toolkit.register_tool_function(f, name="t", namesake_strategy=NAMESAKE_RENAME)
    require(renamed is not None and renamed.name != "t", "rename 策略未生成新名字")


# ── L1-1.3 结果解析 ─────────────────────────────────────────────
@case(
    "L1-1.3",
    "同步与异步工具函数都能被统一异步入口正确调用并返回结果",
    title="工具结果解析错误",
    dimension="MCP - 结果解析",
    severity="P1",
    threshold="≥ 98%",
    counter_example="异步函数返回 awaitable 而非结果 → 结果被当作对象透传",
    tags=("黄金",),
)
def test_sync_and_async_tool_execution():
    from domains.tool.core.application.toolkit import Toolkit

    toolkit = Toolkit()
    toolkit.register_tool_function(lambda a, b: a + b, name="sync_add")

    async def _mul(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a * b

    toolkit.register_tool_function(_mul, name="async_mul")

    require_eq(asyncio.run(toolkit.execute_tool("sync_add", {"a": 1, "b": 2})), 3, "同步工具结果")
    require_eq(asyncio.run(toolkit.execute_tool("async_mul", {"a": 3, "b": 4})), 12, "异步工具结果")


@case(
    "L1-1.3",
    "MCP 工具登记链路：假 manager 的工具被正确注册进 Toolkit 并标记来源为 mcp",
    title="MCP 工具登记链路断裂",
    dimension="MCP - 结果解析",
    severity="P1",
    threshold="100%",
    counter_example="登记后 toolkit 中无该工具 → MCP 能力不可用",
    tags=("黄金",),
)
def test_mcp_tools_registered_into_toolkit():
    from domains.agent.application.toolkit.mcp_tool_registration import AgentMcpToolRegistrar
    from domains.tool.core.application.registration import ToolRegistryService
    from domains.tool.core.application.toolkit import Toolkit

    class _FakeManager:
        class mcp_registry:  # noqa: N801
            @staticmethod
            def names():
                return ("fake-srv",)

        @staticmethod
        def get_tools(server_name):
            return {"echo": lambda text: f"echo:{text}"}

    toolkit = Toolkit()
    sources = ToolRegistryService()
    result = asyncio.run(
        AgentMcpToolRegistrar(_FakeManager()).register(toolkit, sources, agent_name="a1")
    )

    require_eq(result.registered_tool_names, ("echo",), "已登记工具名")
    require_eq(result.mcp_server_names, ("fake-srv",), "已登记服务名")
    require_eq(result.warnings, (), "告警列表")
    require("echo" in toolkit.tool_names(), "Toolkit 中的工具名")
    require_eq(str(sources.source_of("echo")), "mcp", "工具来源标记")


@case(
    "L1-1.4",
    "MCP 登记在 manager 缺失或为 None 时安全降级，不抛异常",
    title="MCP 登记缺少安全降级",
    dimension="MCP - 失败处理",
    severity="P0",
    threshold="100% 不抛异常",
    counter_example="manager=None 时抛异常 → 整个装配流程崩溃",
    tags=("故障",),
)
def test_mcp_registration_degrades_safely():
    from domains.agent.application.toolkit.mcp_tool_registration import AgentMcpToolRegistrar
    from domains.tool.core.application.toolkit import Toolkit

    result = asyncio.run(AgentMcpToolRegistrar(None).register(Toolkit(), None))
    require(not bool(result), "manager=None 时应返回空结果")
    require_eq(result.registered_tool_names, (), "已登记工具名")


# ── L1-1.4 失败处理 ─────────────────────────────────────────────
@case(
    "L1-1.4",
    "调用未注册工具时抛出明确的 KeyError，而非静默返回空",
    title="未注册工具调用被静默忽略",
    dimension="MCP - 失败处理",
    severity="P1",
    threshold="100%",
    counter_example="未注册工具返回 None → 模型误以为调用成功",
    tags=("故障",),
)
def test_unregistered_tool_raises():
    from domains.tool.core.application.toolkit import Toolkit

    with pytest.raises(KeyError):
        asyncio.run(Toolkit().execute_tool("nope", {}))


@case(
    "L1-1.4",
    "工具函数抛出异常时异常向上传播，不被静默吞掉（不伪造成功结果）",
    title="工具异常被静默吞掉",
    dimension="MCP - 失败处理",
    severity="P0",
    threshold="100% 不静默失败",
    counter_example="工具异常被吞掉并返回 None → 模型认为调用成功",
    tags=("故障", "P0"),
)
def test_tool_exception_not_swallowed():
    from domains.tool.core.application.toolkit import Toolkit

    toolkit = Toolkit()

    def _boom(text: str) -> str:
        raise RuntimeError("工具内部失败")

    toolkit.register_tool_function(_boom, name="boom")
    with pytest.raises(RuntimeError, match="工具内部失败"):
        asyncio.run(toolkit.execute_tool("boom", {"text": "x"}))


# ── L1-1.7 并发安全 ─────────────────────────────────────────────
@case(
    "L1-1.7",
    "并发调用同一工具 50 次，结果不串、不丢（每次返回各自输入的正确结果）",
    title="并发工具调用结果串扰",
    dimension="MCP - 并发安全",
    severity="P1",
    threshold="≥ 99%",
    counter_example="并发下结果错配 → 工具返回了别的调用的数据",
    tags=("故障",),
)
def test_concurrent_tool_calls_isolated():
    from domains.tool.core.application.toolkit import Toolkit

    toolkit = Toolkit()
    toolkit.register_tool_function(lambda n: n * 2, name="double")

    async def _run():
        return await asyncio.gather(
            *(toolkit.execute_tool("double", {"n": i}) for i in range(50))
        )

    results = asyncio.run(_run())
    expected = [i * 2 for i in range(50)]
    mismatches = [
        (i, got, want) for i, (got, want) in enumerate(zip(results, expected)) if got != want
    ]
    require(
        not mismatches,
        f"{len(mismatches)}/50 次并发调用返回了错误结果",
        detail=f"前3 个错配：{mismatches[:3]}",
    )


@case(
    "L1-1.7",
    "并发调用不同工具时各自结果互不干扰",
    title="并发多工具调用结果串扰",
    dimension="MCP - 并发安全",
    severity="P1",
    threshold="100%",
    counter_example="并发下工具名与结果错配",
    tags=("故障",),
)
def test_concurrent_different_tools_isolated():
    from domains.tool.core.application.toolkit import Toolkit

    toolkit = Toolkit()
    toolkit.register_tool_function(lambda: "A" * 10, name="tool_a")
    toolkit.register_tool_function(lambda: "B" * 10, name="tool_b")

    async def _run():
        return await asyncio.gather(
            *(toolkit.execute_tool("tool_a" if i % 2 else "tool_b", {}) for i in range(40))
        )

    results = asyncio.run(_run())
    bad = [
        (i, r) for i, r in enumerate(results) if not r.startswith("A" if i % 2 else "B")
    ]
    require(not bad, f"{len(bad)}/40 次并发调用返回了错误的工具结果", detail=str(bad[:3]))


# ── 工具来源登记 ────────────────────────────────────────────────
@case(
    "L1-1.1",
    "工具来源登记完整：builtin / mcp / skill / agent 四类来源可区分",
    title="工具来源无法区分",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="来源均为 unknown → 无法按来源做权限管控",
    tags=("黄金",),
)
def test_tool_source_registration():
    from domains.tool.core.application.registration import ToolRegistryService, ToolSource

    svc = ToolRegistryService()
    svc.register_builtin_tool("calc")
    svc.register_mcp_tool("fs_read", origin="local-fs")
    svc.register_skill_tool("report")
    svc.register_subagent_tool("delegate")

    require_eq(svc.source_of("calc"), ToolSource.BUILTIN, "内置工具来源")
    require_eq(svc.source_of("fs_read"), ToolSource.MCP, "MCP 工具来源")
    require_eq(svc.source_of("report"), ToolSource.SKILL, "skill 工具来源")
    require_eq(svc.source_of("delegate"), ToolSource.AGENT, "子agent 工具来源")
    require_eq(svc.source_of("never_registered"), ToolSource.UNKNOWN, "未登记工具的默认来源")
    require_eq(svc.origin_of("fs_read"), "local-fs", "工具来源的 origin 信息")


@case(
    "L1-1.1",
    "已知来源不被后续的 unknown 登记覆盖",
    title="已知工具来源被 unknown 覆盖",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="MCP 工具被重新登记后来源变为 unknown → 权限管控失效",
    tags=("故障",),
)
def test_known_source_not_overwritten():
    from domains.tool.core.application.registration import ToolRegistryService, ToolSource

    svc = ToolRegistryService()
    svc.register_mcp_tool("fs_read")
    svc.register_tool("fs_read", source=ToolSource.UNKNOWN)
    require_eq(svc.source_of("fs_read"), ToolSource.MCP, "重复登记后的工具来源")


# ── 真实 MCP 连接（需外部服务） ─────────────────────────────────
@pytest.mark.skip(reason="L1-1.1/G1 真实 MCP 连接需 fastmcp 与外部 MCP server"
                "（指南 L6：需可控外部服务），离线环境不满足")
@case(
    "L1-1.1",
    "能连接真实 MCP server 并列举其工具（tools/list）",
    title="真实 MCP server 连接失败（需外部服务）",
    dimension="MCP - 工具发现",
    severity="P1",
    threshold="100%",
    counter_example="连接失败或 tools/list 返回空 → MCP 能力不可用",
    tags=("黄金",),
)
def test_real_mcp_server_connection():
    from domains.protocol.mcp.client import FASTMCP_AVAILABLE, MCPClient

    if not FASTMCP_AVAILABLE:
        pytest.skip("需要 fastmcp")
    raise NotImplementedError(
        "需提供一个可控的 MCP server（如 python -m mcp_server_filesystem）后实现"
    )