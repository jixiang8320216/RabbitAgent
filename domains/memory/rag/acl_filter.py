"""RAG 检索结果的访问控制（ACL）过滤。

背景（修复记录）
----------------
验收项 L1-5.4（P0，权限过滤）此前判为 BLOCKED，原因是
``memory/rag/pipeline.py`` 的 ``search_vectors`` 只支持
``memory_type`` / ``is_rag_data`` / ``data_source`` / ``rag_namespace``
四个过滤维度，**没有任何按用户 / 角色 / 归属过滤的入口**——
这意味着任何用户的检索都可能召回别人的私有文档，
属于真实的越权读取风险，而非单纯的「无法验证」。

本模块提供纯函数式过滤层，可在检索后叠加，也可在入库前打标：

    from memory.rag.acl_filter import filter_by_acl, ACLContext

    visible = filter_by_acl(hits, ACLContext(user_id="u1", roles={"staff"}))
    # 不在可见集合内的文档会被剔除，且剔除是**默认可观测**的：
    # 返回值附带 FilterReport，越权条目数不会被静默吞掉。
"""

from __future__ import annotations

import typing
from dataclasses import dataclass, field

__all__ = [
    "ACLContext",
    "FilterReport",
    "document_acl",
    "is_visible",
    "filter_by_acl",
]


@dataclass(frozen=True)
class ACLContext:
    """一次检索的调用者身份。

    :param user_id:调用者 id
    :param roles:  调用者角色集合
    :param tenants: 调用者所属租户/组织，用于多租户隔离
    """

    user_id: str = ""
    roles: frozenset = frozenset()
    tenants: frozenset = frozenset()


@dataclass(frozen=True)
class FilterReport:
    """过滤结果的可观测信息。

    「越权条目被静默丢弃」是权限系统最危险的失败模式——上层看不到
    任何异常，只会以为「就是没搜到」。因此这里强制暴露计数。
    """

    kept: int = 0
    dropped: int = 0
    dropped_ids: tuple = ()
    reason: str = ""

    @property
    def had_violation(self) -> bool:
        return self.dropped > 0

    def summary(self) -> str:
        return (f"保留 {self.kept} 条，越权剔除 {self.dropped} 条"
                + (f"（ids={list(self.dropped_ids[:5])}）"
                   if self.dropped_ids else ""))


def _as_set(value: typing.Any) -> frozenset:
    if value is None:
        return frozenset()
    if isinstance(value, (str, bytes)):
        return frozenset({value})
    try:
        return frozenset(value)
    except TypeError:
        return frozenset({value})


def document_acl(document: typing.Mapping) -> frozenset:
    """取出一条文档上记录的**允许访问者**集合。

    支持三种常见标注写法（取并集，兼容不同入库端）：

    - ``acl_users``  : ["u1", "u2"]
    - ``acl_roles``  : ["staff", "admin"]
    - ``acl_tenants``: ["org_a"]

    未标注任何 ACL 的文档视为**公开**（public），由调用方通过
    :func:`filter_by_acl` 的 ``default_deny`` 参数决定是否放行。
    """
    out: set = set()
    for key in ("acl_users", "acl_roles", "acl_tenants"):
        out |= _as_set(document.get(key))
    return frozenset(out)


def is_visible(
    document: typing.Mapping,
    context: ACLContext,
    default_deny: bool = False,
) -> bool:
    """判断单条文档对给定身份是否可见。

    判定顺序：

    1. 显式 ``visible_to=False`` -> 一律不可见（显式拒绝优先）
    2. 无任何 ACL 标注 -> 按 ``default_deny`` 决定
    3. 调用者出现在 acl_users / acl_roles / acl_tenants 任一集合 -> 可见
    4. 否则不可见
    """
    if document.get("visible_to") is False:
        return False

    users = _as_set(document.get("acl_users"))
    roles = _as_set(document.get("acl_roles"))
    tenants = _as_set(document.get("acl_tenants"))
    if not (users or roles or tenants):
        return not default_deny

    if context.user_id and context.user_id in users:
        return True
    if context.roles and (context.roles & roles):
        return True
    if context.tenants and (context.tenants & tenants):
        return True
    return False


def filter_by_acl(
    documents: typing.Iterable[typing.Mapping],
    context: ACLContext,
    default_deny: bool = False,
) -> tuple[list, FilterReport]:
    """按 ACL 过滤检索结果。

    :param documents:  检索命中的文档列表
    :param context:    调用者身份
    :param default_deny: 无 ACL 标注的文档是否默认拒绝。
                         安全敏感场景应传 True。
    :return:(过滤后列表, 过滤报告)
    """
    kept: list = []
    dropped_ids: list = []
    for doc in documents or ():
        if is_visible(doc, context, default_deny):
            kept.append(doc)
        else:
            identifier = (doc.get("id") or doc.get("chunk_id")
                          or doc.get("doc_id") or "?")
            dropped_ids.append(str(identifier))

    return kept, FilterReport(
        kept=len(kept),
        dropped=len(dropped_ids),
        dropped_ids=tuple(dropped_ids),
        reason="ACL 过滤（越权条目不得进入模型上下文）",
    )