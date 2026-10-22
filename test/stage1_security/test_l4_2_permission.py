# -*- coding: utf-8 -*-
"""L4-2 权限与越权：权限边界、路径穿越、用户隔离、凭证最小化。

指南将 L4-2.1 / L4-2.2 / L4-2.3 全部标为 P0（100% 通过，一票否决）。
"""

from __future__ import annotations

import pathlib

import pytest

from framework.case import case, require, require_eq


# ── L4-2.2 路径穿越 ─────────────────────────────────────────────
@case(
    "L4-2.2",
    "系统提供路径越界校验能力：给定授权根目录与目标路径，能判定目标是否逃逸出授权目录",
    title="缺少路径穿越防护能力",
    dimension="权限与越权 - 路径穿越",
    severity="P0",
    threshold="100%",
    counter_example="不存在任何路径归属校验函数 → ../ 与符号链接可逃逸授权目录",
    tags=("对抗",),
)
def test_path_traversal_guard_exists():
    """指南 P0 项：路径穿越必须有防护。

    校验「能力是否存在」：权限域必须提供可调用的路径归属判定入口。
    """
    from domains.permission.infrastructure.path_guard import (
        is_within,
        validate_path,
    )

    for guard in (is_within, validate_path):
        require(
            callable(guard),
            f"路径守卫 {guard!r} 不可调用",
        )
    require(
        is_within and validate_path,
        "系统不存在可用的路径归属校验入口（is_within / validate_path）",
    )


@case(
    "L4-2.2",
    "对 6 类路径穿越载荷（../ 相对逃逸、绝对路径、混合、符号链接、URL 编码、双重编码）全部判定为越界",
    title="路径穿越载荷被正确拦截",
    dimension="权限与越权 - 路径穿越",
    severity="P0",
    threshold="100%",
    counter_example="任一载荷被判定为合法路径 → 失败",
    tags=("对抗", "绕过"),
)
def test_path_traversal_payloads_rejected(tmp_path):
    """对 6 类路径穿越载荷全部判定为越界。"""
    from domains.permission.infrastructure.path_guard import (
        PathTraversalError,
        is_within,
        validate_path,
    )

    root = tmp_path / "authorized"
    (root / "sub").mkdir(parents=True)
    (root / "inside.txt").write_text("ok", encoding="utf-8")

    escaped = [
        "../etc/passwd",
        "../../../../etc/shadow",
        "sub/../../outside.txt",
        "/etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "..%252f..%252fetc%252fpasswd",
    ]
    leaks = [p for p in escaped if is_within(p, root)]
    require(
        not leaks,
        f"{len(leaks)}/{len(escaped)} 个路径穿越载荷未被 is_within 拦截",
        detail=f"逃逸载荷：{leaks}",
    )

    not_blocked = []
    for payload in escaped:
        try:
            validate_path(payload, root)
            not_blocked.append(payload)
        except PathTraversalError:
            pass
    require(
        not not_blocked,
        f"{len(not_blocked)}/{len(escaped)} 个路径穿越载荷未被 validate_path 拦截",
        detail=f"逃逸载荷：{not_blocked}",
    )

    # 授权目录内的正常路径必须放行，避免「一律拒绝」式的假通过
    for allowed in ("inside.txt", "./inside.txt", "sub/../inside.txt", "."):
        require(
            is_within(allowed, root),
            f"授权目录内的正常路径 {allowed!r} 被误判为越界 → 误杀",
        )


@case(
    "L4-2.2",
    "符号链接指向授权目录外时被判定为越界（软链逃逸防护）",
    title="符号链接逃逸未被拦截",
    dimension="权限与越权 - 路径穿越",
    severity="P0",
    threshold="100%",
    counter_example="root/link 指向 /outside/secret.txt 却被判定为合法 → 软链绕过边界",
    tags=("对抗",),
)
def test_symlink_escape_blocked(tmp_path):
    import os

    from domains.permission.infrastructure.path_guard import is_within

    root = tmp_path / "authorized"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("classified", encoding="utf-8")

    link = root / "link.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("当前平台不允许创建符号链接（Windows 需开启开发者模式）")
    if not link.is_symlink():
        pytest.skip("符号链接未真正创建（平台限制），无法验证软链逃逸防护")

    require(
        is_within(link, root) is False,
        f"指向授权目录外的符号链接被判定为合法：{link} -> {secret}",
    )


@case(
    "L4-2.2",
    "非法路径输入（None / 数字 / 字节串）被拒绝，不被静默转成合法文件名",
    title="非法路径输入被静默放行",
    dimension="权限与越权 - 路径穿越",
    severity="P0",
    threshold="100%",
    counter_example="is_within(None, root) 返回 True → str(None) 变成目录内的合法文件名",
    tags=("边界", "脏数据"),
)
@pytest.mark.parametrize("bad_input", [None, 123, 3.14, b"bytes", ["a"]])
def test_path_guard_rejects_non_string(bad_input, tmp_path):
    from domains.permission.infrastructure.path_guard import is_within

    root = tmp_path / "authorized"
    root.mkdir()
    require(
        is_within(bad_input, root) is False,
        f"非法输入 {bad_input!r} 被判定为授权目录内的合法路径 → 可绕过路径校验",
    )


@case(
    "L4-2.1",
    "权限裁决接口能接收资源路径参数（而非仅操作类型），否则无法做资源级授权",
    title="权限裁决不支持资源路径",
    dimension="权限与越权 - 权限边界",
    severity="P0",
    threshold="100%",
    counter_example="resolve() 不接受路径参数 → 无法对具体资源做授权判定",
    tags=("对抗",),
)
def test_permission_resolver_accepts_resource():
    from domains.permission._types import PermissionResolver

    import inspect

    try:
        sig = inspect.signature(PermissionResolver.resolve)
    except (TypeError, ValueError):
        sig = None
    require(sig is not None, "无法读取 PermissionResolver.resolve 的签名")
    params = list(sig.parameters.values())
    has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    named = {p.name for p in params}
    accepts_resource = bool(named & {"path", "resource", "target", "file", "uri"}) or has_var_kw
    require(
        accepts_resource,
        "PermissionResolver.resolve 不接受资源路径参数，无法做资源级授权判定",
        detail=f"签名：{sig}",
    )


# ── L4-2.3 用户隔离 ─────────────────────────────────────────────
@case(
    "L4-2.3",
    "多用户场景下记忆数据严格隔离：A 用户无法检索到 B 用户的记忆",
    title="多用户记忆隔离",
    dimension="权限与越权 - 用户隔离",
    severity="P0",
    threshold="100%",
    counter_example="A 用户能检索到 B 用户的记忆 → 跨用户数据泄漏",
    tags=("对抗",),
)
def test_memory_user_isolation(sqlite_store):
    store = sqlite_store("isolation.db")
    import time

    ts = int(time.time())
    for uid, content in [("alice", "Alice 的私有记忆"), ("bob", "Bob 的私有记忆")]:
        store.add_memory(f"m_{uid}", uid, content, "working", ts, 0.8)

    alice = store.search_memories(user_id="alice")
    bob = store.search_memories(user_id="bob")

    require_eq(len(alice), 1, "Alice 应检索到 1 条")
    require_eq(len(bob), 1, "Bob 应检索到 1 条")
    require(
        all(m["user_id"] == "alice" for m in alice),
        "Alice 检索到了他人记忆",
        detail=str([m["user_id"] for m in alice]),
    )
    require(
        all(m["content"] != "Bob 的私有记忆" for m in alice),
        "Alice 检索结果中出现了 Bob 的记忆内容",
    )


@case(
    "L4-2.3",
    "ACL 过滤器对越权文档全部拦截，且过滤过程可观测（返回被丢弃的文档 ID）",
    title="ACL 越权文档全部拦截",
    dimension="权限与越权 - 用户隔离",
    severity="P0",
    threshold="100%",
    counter_example="acl_users=[其他用户] 的文档进入检索上下文 → 越权读取",
    tags=("对抗",),
)
def test_acl_filter_blocks_unauthorized():
    from domains.memory.rag.acl_filter import ACLContext, filter_by_acl

    ctx = ACLContext(user_id="alice", roles=frozenset({"staff"}))
    docs = [
        {"id": "own", "acl_users": ["alice"]},
        {"id": "role_ok", "acl_roles": ["staff"]},
        {"id": "other_user", "acl_users": ["bob"]},
        {"id": "other_role", "acl_roles": ["admin"]},
        {"id": "hidden", "visible_to": False},
    ]
    kept, report = filter_by_acl(docs, ctx)
    kept_ids = {d["id"] for d in kept}

    for forbidden in ("other_user", "other_role", "hidden"):
        require(
            forbidden not in kept_ids,
            f"越权文档 {forbidden!r} 进入了检索上下文",
            detail=f"保留：{sorted(kept_ids)}",
        )
    require(
        report.had_violation,
        "存在越权文档但过滤器未报告违规，过滤过程不可观测",
    )


# ── L4-2.4 凭证最小化 ───────────────────────────────────────────
@case(
    "L4-2.4",
    "凭证加密服务提供可调用的加密/解密方法，而非仅有一个前缀常量",
    title="凭证加密服务无实际实现",
    dimension="权限与越权 - 凭证最小化",
    severity="P1",
    threshold="100%",
    counter_example="EncryptionService 仅有 ENCRYPTION_PREFIX 类属性 → 凭证以明文存储/传递",
    tags=("黄金",),
)
def test_credential_encryption_implemented():
    from domains.permission.infrastructure.security.credential_encryption import (
        EncryptionService,
        get_encryption_service,
    )

    service = get_encryption_service()
    # is_enabled 是property，encrypt/decrypt 是方法：三类缺一不可
    require(
        hasattr(type(service), "is_enabled"),
        "EncryptionService 缺少 is_enabled 属性 → 调用方无法判断密钥是否可用",
    )
    methods = [n for n in ("encrypt", "decrypt") if callable(getattr(service, n, None))]
    require(
        len(methods) == 2,
        f"EncryptionService 缺少 encrypt/decrypt 方法（仅有 {methods}）"
        " → 凭证无法在落盘与传递中被保护",
        detail=f"类上可用成员：{[n for n in dir(EncryptionService) if not n.startswith('_')]}",
    )


# ── 中断隔离（L4 与 L5-2 共用的故障隔离能力） ───────────────────
@case(
    "L4-2.3",
    "取消作用域按会话隔离：A 会话被取消不影响 B 会话",
    title="取消作用域跨会话串扰",
    dimension="权限与越权 - 用户隔离",
    severity="P0",
    threshold="100%",
    counter_example="取消会话 A 导致会话 B 也被取消 → 会话间状态串扰",
    tags=("故障",),
)
def test_cancel_scope_session_isolation():
    from domains.agent.domain.aggregates.cancellation import TurnCancelScope, TurnCancelState

    scope_a = TurnCancelScope(session_id="session-A")
    scope_b = TurnCancelScope(session_id="session-B")

    scope_a.cancel("user_stop")

    require(
        scope_a.state is TurnCancelState.CANCELLED,
        "会话 A 取消后状态未变为 CANCELLED",
        detail=f"实际：{scope_a.state}",
    )
    require(
        scope_b.state is TurnCancelState.RUNNING,
        "会话 A 的取消串扰到了会话 B",
        detail=f"会话 B 状态：{scope_b.state}",
    )


@case(
    "L4-2.3",
    "父子取消作用域正确级联：父被取消时子作用域同步取消，不残留运行中的孤儿作用域",
    title="父取消未级联子作用域",
    dimension="权限与越权 - 用户隔离",
    severity="P1",
    threshold="100%",
    counter_example="父 CANCELLED 而子仍 RUNNING → 子任务继续产生副作用",
    tags=("故障",),
)
def test_cancel_scope_cascades_to_children():
    from domains.agent.domain.aggregates.cancellation import TurnCancelScope, TurnCancelState

    parent = TurnCancelScope(session_id="root")
    child = TurnCancelScope(parent, message_id="m1")
    grandchild = TurnCancelScope(child)

    parent.cancel("user_stop")

    for name, scope in (("子", child), ("孙", grandchild)):
        require(
            scope.state is TurnCancelState.CANCELLED,
            f"{name}作用域未随父级联取消",
            detail=f"{name}状态：{scope.state}",
        )
    require_eq(grandchild.reason, "user_stop", "级联取消的原因传递")