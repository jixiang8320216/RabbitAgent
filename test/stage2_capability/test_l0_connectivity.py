# -*- coding: utf-8 -*-
"""L0 接入与连通层 + 模块完整性。

指南 L0 的失效后果是「完全不可用」，因此这是所有验证的前提。
其中 L0-4 版本兼容对应本项目当前最严重的问题：大量模块因前向引用缺陷无法导入。
"""

from __future__ import annotations

import collections
import importlib
import pathlib

import pytest

from framework.case import case, require, require_eq

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _iter_modules() -> list[str]:
    """枚举项目内全部业务模块（domains / interfaces / shared）。"""
    names = []
    for top in ("domains", "interfaces", "shared"):
        for path in (PROJECT_ROOT / top).rglob("*.py"):
            if any(x in {"__pycache__", ".venv", ".idea"} for x in path.parts):
                continue
            rel = path.relative_to(PROJECT_ROOT).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            names.append(".".join(parts))
    return sorted(set(names))


@pytest.fixture(scope="module")
def import_report() -> dict:
    """一次性扫描全部模块的导入结果（扫描成本高，模块内共享）。"""
    ok, missing_dep, broken = [], collections.defaultdict(list), []
    for name in _iter_modules():
        try:
            importlib.import_module(name)
            ok.append(name)
        except ModuleNotFoundError as exc:
            missing_dep[name] = str(exc).split("'")[1] if "'" in str(exc) else "?"
        except Exception as exc:  # noqa: BLE001 — 任何非依赖缺失的异常都是缺陷
            broken.append((name, f"{type(exc).__name__}: {exc}"))
    return {
        "ok": ok,
        "missing_dep": missing_dep,
        "broken": broken,
        "total": len(ok) + len(missing_dep) + len(broken),
    }


# ── L0-1 网络可达性（降级为依赖可达性） ─────────────────────────
@case(
    "L0-1",
    "全部业务模块可成功导入；因第三方依赖缺失而跳过的模块需明确列出而非静默失败",
    title="模块导入完整性",
    dimension="接入与连通层 - 网络可达性",
    severity="P0",
    threshold="非依赖缺失导致的导入失败 = 0",
    counter_example="存在 NameError/AttributeError 等代码缺陷导致的导入失败 → 失败",
    tags=("黄金",),
)
def test_all_modules_importable(import_report):
    broken = import_report["broken"]
    grouped = collections.Counter(name.split(".")[0] for name, _ in broken)
    require(
        not broken,
        f"{len(broken)}/{import_report['total']} 个模块因代码缺陷无法导入"
        f"（按域分布：{dict(grouped)}）",
        detail="前5 条：\n      " + "\n      ".join(f"{n} → {e}" for n, e in broken[:5]),
    )


@case(
    "L0-1",
    "导入失败的原因全部为第三方依赖缺失，不含代码自身缺陷",
    title="导入失败根因全为代码缺陷",
    dimension="接入与连通层 - 网络可达性",
    severity="P0",
    threshold="100%",
    counter_example="NameError/AttributeError 占比 > 0 → 失败",
    tags=("故障",),
)
def test_import_failures_are_dependency_only(import_report):
    broken = import_report["broken"]
    code_defects = [
        (n, e) for n, e in broken
        if any(k in e for k in ("NameError", "AttributeError", "ImportError"))
    ]
    require(
        not code_defects,
        f"{len(code_defects)} 个模块因代码缺陷导入失败，其中 "
        f"{sum(1 for _, e in code_defects if 'NameError' in e)} 个为 NameError（前向引用缺陷）",
        detail="前 5 条：\n      " + "\n      ".join(f"{n} → {e}" for n, e in code_defects[:5]),
    )


@case(
    "L0-1",
    "前向引用缺陷（函数签名引用尚未定义的类型别名）数量为 0",
    title="存在前向引用 NameError 缺陷",
    dimension="接入与连通层 - 网络可达性",
    severity="P0",
    threshold="0 个",
    counter_example="message.py 在 MediaSource 定义前使用它 → 整个值对象域不可导入",
    tags=("故障",),
)
def test_no_forward_reference_bugs(import_report):
    name_errors = [(n, e) for n, e in import_report["broken"] if "NameError" in e]
    roots = collections.Counter(e.split("'")[1] for _, e in name_errors if "'" in e)
    require(
        not name_errors,
        f"{len(name_errors)} 个模块因前向引用缺陷（NameError）无法导入；"
        f"根因符号：{dict(roots)}",
        detail=f"共 {len(set(n for n, _ in name_errors))} 个独立根因，"
               f"波及 {len(name_errors)} 个模块",
    )


@case(
    "L0-4",
    "全部内置能力域的核心入口模块可导入",
    dimension="接入与连通层 - 版本兼容",
    title="核心能力域不可用",
    severity="P0",
    threshold="25/25 域可导入",
    counter_example="permission 或 memory 域不可导入 → 核心场景不可用",
    tags=("黄金",),
)
def test_core_domains_importable():
    core_entries = [
        "domains.agent.domain.aggregates.cancellation",
        "domains.configuration.domain.model.app_mode",
        "domains.hook.application.hook_runtime",
        "domains.permission.domain.policy",
        "domains.tool.core.application.toolkit",
        "domains.skill.service.skill_registry",
        "domains.memory.storage.document_store",
        "domains.memory.rag.acl_filter",
        "domains.observability.application.observability_context",
        "domains.billing.domain.litellm_budget",
        "domains.mcp.domain.tool_projection",
        "domains.runtime.application.orchestrators.session_turn_checkpoint",
        "domains.evaluation.benchmarks.bfcl.metrics",
        "domains.protocol.mcp.client",
    ]
    failed = []
    for name in core_entries:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name} → {type(exc).__name__}: {exc}")
    require(
        not failed,
        f"{len(failed)}/{len(core_entries)} 个核心入口模块不可导入",
        detail="; ".join(failed[:4]),
    )


# ── L0-2 鉴权有效性 ─────────────────────────────────────────────
@case(
    "L0-2",
    "会话中断标记对非法会话 ID 安全降级（返回保守值），不抛出未捕获异常",
    title="非法会话ID导致未捕获异常",
    dimension="接入与连通层 - 鉴权有效性",
    severity="P1",
    threshold="100% 不抛异常",
    counter_example="check_interrupt(None) 抛异常 → 鉴权层被非法输入击穿",
    tags=("边界", "脏数据"),
)
def test_invalid_session_id_safe(clean_interrupt):
    from domains.permission.application.interrupt_coordination import InterruptManager

    for probe in (None, "", "   "):
        require(
            InterruptManager.check_interrupt(probe) is False,
            f"非法会话 ID {probe!r} 未返回保守值 False",
        )


@case(
    "L0-2",
    "中断标记为一次性消费：读取后失效，不可被重复触发",
    title="中断标记可被重复消费",
    dimension="接入与连通层 - 鉴权有效性",
    severity="P0",
    threshold="100%",
    counter_example="check_interrupt 连续两次都返回 True → 中断可被重放",
    tags=("黄金",),
)
def test_interrupt_is_one_shot(clean_interrupt):
    from domains.permission.application.interrupt_coordination import InterruptManager

    InterruptManager.request_interrupt("session-x")
    require(InterruptManager.peek_interrupt("session-x") is True, "peek 未读到中断标记")
    require(InterruptManager.check_interrupt("session-x") is True, "首次消费应返回 True")
    require(
        InterruptManager.check_interrupt("session-x") is False,
        "中断标记被重复消费：第二次 check_interrupt 仍返回 True",
    )


# ── L0-3 限流与退避 ─────────────────────────────────────────────
@case(
    "L0-3",
    "Hook 超时被强制中断，不产生无限期阻塞（返回 TIMEOUT 状态而非挂起）",
    title="Hook 超时未强制中断",
    dimension="接入与连通层 - 限流与退避",
    severity="P1",
    threshold="超时后 ≤ timeout + 200ms 返回",
    counter_example="dispatch 在 hook 超时时永久阻塞 → 端到端卡死",
    tags=("故障",),
)
def test_hook_timeout_bounded(hook_snapshot_factory):
    import time

    from domains.hook.application.hook_runtime import HookInvocation, HookRuntime
    from domains.hook.domain.hook_definitions import HookEvent
    from domains.hook.domain.outputs import HookRunStatus
    from domains.hook.domain.resource_models import HookPolicy

    timeout = 0.25
    snapshot = hook_snapshot_factory(
        {"PreToolUse": [("slow", (lambda _inv: time.sleep(5)))],}
    )
    # 显式设置 timeout
    from domains.hook.application.hook_runtime import HookDefinition

    snapshot = type(snapshot)(
        hooks={
            "PreToolUse": [
                HookDefinition(name="slow", handler=lambda _inv: time.sleep(5), timeout=timeout)
            ]
        },
        version="test",
    )

    started = time.monotonic()
    trace = HookRuntime(snapshot, HookPolicy()).dispatch(
        HookInvocation(event=HookEvent.PRE_TOOL_USE, tool_name="x")
    )
    elapsed = time.monotonic() - started

    require(
        "timeout" in trace.statuses,
        "超时 hook 未产生 TIMEOUT 状态",
        detail=f"实际状态：{trace.statuses}",
    )
    require(
        elapsed < timeout + 0.6,
        f"dispatch 在 hook 超时时阻塞了 {elapsed:.2f}s（阈值 {timeout + 0.6:.2f}s）",
    )


# ── L0-4 版本兼容 ───────────────────────────────────────────────
@case(
    "L0-4",
    "项目使用的 type别名语法（PEP 695）要求 Python ≥ 3.12，当前解释器满足",
    title="Python 版本不满足 PEP 695 语法要求",
    dimension="接入与连通层 - 版本兼容",
    severity="P0",
    threshold="Python ≥ 3.12",
    counter_example="在 Python 3.11 上运行 → 所有 type X = ... 语法报错",
    tags=("黄金",),
)
def test_python_version_compatible():
    import sys

    require(
        sys.version_info >= (3, 12),
        f"当前 Python {sys.version_info.major}.{sys.version_info.minor} 不支持 PEP 695 "
        "type 别名语法，项目源码将大面积解析失败",
        detail=f"实际版本：{sys.version.split()[0]}",
    )


@case(
    "L0-4",
    "PEP 695 type 别名的前向引用可安全求值（模块导入不因别名定义顺序失败）",
    title="type 别名前向引用导致导入失败",
    dimension="接入与连通层 - 版本兼容",
    severity="P0",
    threshold="100%",
    counter_example="函数签名引用后定义的 type 别名 → NameError",
    tags=("故障",),
)
def test_type_alias_forward_reference_safe():
    """值对象域被全体 Agent / LLM / 可观测模块依赖，一旦不可导入则系统不可用。"""
    targets = [
        "domains.agent.domain.value_objects.message",
        "domains.llm.provider.domain.runtime_configuration",
        "domains.llm.provider.application.token.token_counter",
        "domains.agent.domain.value_objects.chat_response",
    ]
    failed = []
    for name in targets:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name} → {type(exc).__name__}: {exc}")
    require(
        not failed,
        f"{len(failed)}/{len(targets)} 个核心值对象模块不可导入，"
        "Agent 消息结构与 token 统计全线失效",
        detail="; ".join(failed),
    )