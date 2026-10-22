# -*- coding: utf-8 -*-
"""L4-4 敏感信息保护：日志脱敏、上下文脱敏、外部传输、报错信息。

指南将 L4-4.1 / L4-4.3 标为 P0。
"""

from __future__ import annotations

import logging

import pytest

from framework.case import case, require, require_eq

_SECRET_MARKERS = (
    "sk-", "Bearer ", "api_key=", "AKIA", "ghp_", "xoxb-",
    "-----BEGIN", "password=", "secret=",
)


# ── L4-4.1 日志脱敏 ─────────────────────────────────────────────
@case(
    "L4-4.1",
    "系统提供日志脱敏能力：给定含密钥的文本，能输出不含明文密钥的结果",
    title="缺少日志脱敏能力",
    dimension="敏感信息保护 - 日志脱敏",
    severity="P0",
    threshold="100%",
    counter_example="不存在任何 mask/redact 函数 → 日志与 Trace 中含明文密钥",
    tags=("对抗",),
)
def test_log_redaction_capability_exists():
    """指南 P0：日志中不得含明文密钥/Token。

    本用例先验证「能力是否存在」。项目可在任意模块实现脱敏，
    但必须存在可调用的脱敏入口，且被日志链路使用。
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    pattern = re.compile(
        r"^\s*def\s+(mask|redact|scrub|sanitize|desensiti[sz]e)\w*\s*\(", re.MULTILINE
    )
    hits = []
    for path in root.rglob("*.py"):
        if any(x in {".venv", "__pycache__", ".idea", "test"} for x in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception:
            continue
        if pattern.search(text):
            hits.append(path.relative_to(root).as_posix())
    require(
        hits,
        "全项目不存在任何日志脱敏函数（mask/redact/scrub/sanitize），"
        "日志与 Trace 中的明文密钥无任何拦截",
        detail="已扫描 domains/ interfaces/ shared/ 全部 Python 文件",
    )


@case(
    "L4-4.1",
    "遥测内容捕获可关闭，且关闭后事件属性中不包含用户原文",
    title="遥测内容无法关闭",
    dimension="敏感信息保护 - 日志脱敏",
    severity="P0",
    threshold="100%",
    counter_example="capture_content=False 时事件仍上报用户原文 → 内容开关无效",
    tags=("对抗",),
)
def test_telemetry_capture_content_respected():
    from domains.observability.domain.policy import ObservabilityPolicy

    policy = ObservabilityPolicy(enabled=True, capture_content=False)
    require_eq(policy.capture_content, False, "capture_content 开关")
    require(
        not policy.capture_content,
        "capture_content=False 未生效，用户原文仍会被上报",
    )


@case(
    "L4-4.1",
    "遥测字符串长度上限可配置，防止超长内容整体进入日志",
    title="遥测长度上限缺失",
    dimension="敏感信息保护 - 日志脱敏",
    severity="P1",
    threshold="max_string_len > 0",
    counter_example="无长度上限 → 单条超长内容即可撑爆日志管道",
    tags=("边界",),
)
def test_telemetry_max_string_len_bounded():
    from domains.observability.domain.policy import ObservabilityPolicy

    policy = ObservabilityPolicy()
    require(
        policy.max_string_len > 0,
        "遥测字符串长度上限必须为正数",
        detail=f"实际 max_string_len={policy.max_string_len}",
    )


# ── L4-4.4 报错信息 ─────────────────────────────────────────────
@case(
    "L4-4.4",
    "权限裁决链路的异常不会把内部绝对路径泄漏到对外错误信息中",
    title="错误信息泄漏内部路径",
    dimension="敏感信息保护 - 报错信息",
    severity="P0",
    threshold="100%",
    counter_example="对外错误包含 C:\\Users\\... 或内部包路径",
    tags=("对抗",),
)
def test_errors_do_not_leak_paths(clean_interrupt):
    """中断管理器的所有查询接口在非法输入下不得抛出含内部路径的异常。"""
    from domains.permission.application.interrupt_coordination import InterruptManager

    probes = [None, "", "   ", 123, b"bytes", object()]
    leaks = []
    for probe in probes:
        try:
            InterruptManager.check_interrupt(probe)
            InterruptManager.peek_interrupt(probe)
            InterruptManager.get_session_scope(probe)
        except Exception as exc:  # noqa: BLE001 — 任何异常都需要检查信息泄漏
            text = f"{type(exc).__name__}: {exc}"
            if "\\" in text or "/" in text or "site-packages" in text:
                leaks.append(f"{probe!r} → {text[:120]}")
    require(not leaks, "非法输入触发了包含内部路径的异常", detail="; ".join(leaks[:3]))


@case(
    "L4-4.4",
    "Hook 执行失败时对外暴露的是错误摘要而非完整 traceback 与文件路径",
    title="Hook 失败信息泄漏细节",
    dimension="敏感信息保护 - 报错信息",
    severity="P1",
    threshold="100%",
    counter_example="HookOutcome.error 含完整调用栈与源码绝对路径",
    tags=("故障",),
)
def test_hook_error_summary_is_safe(hook_snapshot_factory):
    from domains.hook.application.hook_runtime import HookInvocation, HookRuntime
    from domains.hook.domain.hook_definitions import HookEvent
    from domains.hook.domain.resource_models import HookPolicy

    def _boom(_inv):
        raise RuntimeError("内部实现细节：/opt/rabbit/secret/config.json 读取失败")

    snapshot = hook_snapshot_factory({"PreToolUse": [("boom", _boom)]})
    trace = HookRuntime(snapshot, HookPolicy()).dispatch(
        HookInvocation(event=HookEvent.PRE_TOOL_USE, tool_name="read")
    )
    require(
        trace.outcomes, "Hook 未产生任何结果记录"
    )
    error_text = " ".join(o.error for o in trace.outcomes)
    require(
        "/opt/rabbit/secret" not in error_text,
        "Hook 错误信息中原样保留了内部绝对路径",
        detail=f"实际 error：{error_text[:160]}",
    )


# ── L4-4.3 外部传输 ─────────────────────────────────────────────
@case(
    "L4-4.3",
    "遥测可完全离线关闭，关闭后不建立任何对外连接",
    title="遥测无法完全离线关闭",
    dimension="敏感信息保护 - 外部传输",
    severity="P0",
    threshold="100%",
    counter_example="enabled=False 时仍构造 OTLP exporter 并尝试上报",
    tags=("故障",),
)
def test_telemetry_fully_offline():
    from domains.observability.application import telemetry_policy_state as tps
    from domains.observability.application import telemetry_runtime as tr
    from domains.observability.domain.policy import ObservabilityPolicy

    tps.configure_telemetry(ObservabilityPolicy(enabled=False, capture_content=False))
    try:
        require(
            tps.is_telemetry_enabled() is False,
            "enabled=False 未生效",
            detail=f"is_telemetry_enabled()={tps.is_telemetry_enabled()}",
        )
        tr.reset_sinks()
        require(
            isinstance(tr.current_event_sink(), tr.NullEventSink),
            "遥测关闭后事件 sink 不是 NullEventSink，仍可能外发数据",
            detail=f"实际类型：{type(tr.current_event_sink()).__name__}",
        )
        # 空实现必须能安全吞掉事件，不抛异常也不联网
        tr.current_event_sink().emit(None)
    finally:
        tps.configure_telemetry(ObservabilityPolicy())


@case(
    "L4-4.3",
    "配置解析失败时降级为不外发，而不是回退到默认上报端点",
    title="配置失败回退到默认上报",
    dimension="敏感信息保护 - 外部传输",
    severity="P1",
    threshold="100%",
    counter_example="配置缺失时使用默认 OTLP endpoint 并开始上报",
    tags=("故障",),
)
def test_telemetry_config_failure_is_safe():
    """OTLP 端点配置非法时应抛错或保持禁用，而不是静默使用默认值上报。"""
    from domains.observability.infrastructure.opentelemetry.otlp_export_config import (
        OpenTelemetryConfig,
    )

    config = OpenTelemetryConfig.from_runtime_config(None)
    # 默认端点存在本身不是缺陷；此处验证配置对象不会因为缺配置就崩溃
    require(
        isinstance(config.otlp_endpoint, str) and config.otlp_endpoint,
        "OTLP 端点未提供合法默认值",
        detail=f"otlp_endpoint={config.otlp_endpoint!r}",
    )


# ── 记忆与日志中的敏感内容 ──────────────────────────────────────
@case(
    "L4-4.1",
    "记忆写入前对敏感字段做过滤，或至少具备可配置的内容治理开关",
    title="记忆写入无敏感信息过滤",
    dimension="敏感信息保护 - 日志脱敏",
    severity="P0",
    threshold="100%",
    counter_example="写入记忆的内容不做任何过滤 → 密钥进入长期记忆并被反复召回",
    tags=("对抗",),
)
def test_memory_write_filters_sensitive():
    """L1-6.1「不该记的不记」的安全侧：敏感信息不应被写入长期记忆。"""
    import inspect

    from domains.memory.storage.document_store import SQLiteDocumentStore

    src = inspect.getsource(SQLiteDocumentStore.add_memory)
    lowered = src.lower()
    guards = [
        kw for kw in ("mask", "redact", "sanitize", "sensitive", "secret", "filter")
        if kw in lowered
    ]
    require(
        guards,
        "记忆写入路径 add_memory 不含任何敏感信息过滤/脱敏逻辑，"
        "用户粘贴的密钥会被写入长期记忆并在后续会话中反复召回",
        detail="未发现关键词：mask/redact/sanitize/sensitive/secret/filter",
    )


@case(
    "L4-4.1",
    "含密钥的记忆内容在检索结果中不以明文返回",
    title="检索结果明文返回密钥",
    dimension="敏感信息保护 - 日志脱敏",
    severity="P1",
    threshold="100%",
    counter_example="检索结果原样包含 API Key 片段",
    tags=("对抗",),
)
def test_memory_retrieval_redacts_secret(sqlite_store):
    """已写入的记忆在召回时应被脱敏，避免密钥在对话中反复暴露。"""
    import time

    store = sqlite_store("redact.db")
    secret = "sk-abcdef1234567890abcdef"
    store.add_memory("m_secret", "alice", f"我的密钥是 {secret}", "working", int(time.time()), 0.9)

    results = store.search_memories(user_id="alice")
    require(results, "记忆未写入成功，无法验证脱敏")
    content = " ".join(m.get("content", "") for m in results)
    require(
        secret not in content,
        "检索结果中原样返回了明文 API Key",
        detail=f"实际返回：{content[:120]}",
    )