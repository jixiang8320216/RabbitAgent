# -*- coding: utf-8 -*-
"""用例元数据模型与装饰器。

每个测试函数通过 @case(...) 声明自己在验证指南中的位置，pytest 收集阶段
把元数据写入全局 REGISTRY，报告生成阶段据此计算分层得分率与发布门禁。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ── 指南第 1 章：验证分层模型与权重 ────────────────────────────────
LAYER_WEIGHTS: dict[str, int] = {
    "L0": 5,   # 接入与连通层
    "L1": 25,  # 能力层
    "L2": 20,  # 编排层
    "L3": 10,  # 可观测层
    "L4": 25,  # 信任与安全层
    "L5": 15,  # 质量与健壮性层
}

LAYER_NAMES: dict[str, str] = {
    "L0": "接入与连通层",
    "L1": "能力层",
    "L2": "编排层",
    "L3": "可观测层",
    "L4": "信任与安全层",
    "L5": "质量与健壮性层",
}

# 严重级别（附录 C）
SEVERITIES = ("P0", "P1", "P2")

# 判定结果
OUTCOME_PASS = "PASS"
OUTCOME_FAIL = "FAIL"
OUTCOME_SKIP = "SKIP"        # 不适用：需外部服务 / 真实 LLM / 环境不具备
OUTCOME_XFAIL = "XFAIL"      # 已知缺陷（已用 xfail 标记，非阻断）
OUTCOME_ERROR = "ERROR"      # 用例自身异常（fixture 崩溃等）


def normalize_layer(guide_id: str) -> str:
    """从用例编号推断层号：'L4-3.2' -> 'L4'；'L1-1.6' -> 'L1'。"""
    m = re.match(r"^\s*(L[0-5])\b", str(guide_id), re.IGNORECASE)
    return m.group(1).upper() if m else "L1"


@dataclass
class CaseMeta:
    """单条验证用例的完整描述。"""

    guide_id: str                       # 指南中的验证项编号，如 L4-3.2
    title: str                          # 用例标题
    criterion: str                      # 二元判定标准（应做什么）
    layer: str = "L1"
    dimension: str = ""                 # 所属维度，如「危险操作确认 - 确认拦截」
    severity: str = "P1"                 # P0 / P1 / P2
    threshold: str = ""                 # 指南给出的阈值，如 "100%"
    counter_example: str = ""           # 反例（不应出现什么）
    tags: tuple[str, ...] = ()           # 黄金/边界/对抗/故障/拒答（指南 2.2）
    nodeid: str = ""                    # pytest nodeid，运行时填充
    file: str = ""                      # 源码文件，运行时填充
    outcome: str = ""                   # 运行时填充
    duration: float = 0.0               # 运行时填充
    message: str = ""                   # 失败原因 / skip 原因

    def as_dict(self) -> dict[str, Any]:
        return {
            "guide_id": self.guide_id,
            "title": self.title,
            "criterion": self.criterion,
            "layer": self.layer,
            "layer_name": LAYER_NAMES.get(self.layer, self.layer),
            "dimension": self.dimension,
            "severity": self.severity,
            "threshold": self.threshold,
            "counter_example": self.counter_example,
            "tags": list(self.tags),
            "nodeid": self.nodeid,
            "file": self.file,
            "outcome": self.outcome,
            "duration": round(self.duration, 4),
            "message": self.message,
        }


# 全局用例登记表：nodeid -> CaseMeta
REGISTRY: dict[str, CaseMeta] = {}


def collect_meta(item: Any) -> Optional[CaseMeta]:
    """从 pytest item 上取出 @case 元数据（未标注返回 None）。"""
    func = getattr(item, "obj", None) or getattr(item, "function", None)
    meta = getattr(func, "__case__", None)
    if meta is None:
        return None
    meta.nodeid = getattr(item, "nodeid", meta.nodeid)
    meta.file = str(getattr(item, "path", "") or getattr(item, "fspath", ""))
    REGISTRY[meta.nodeid] = meta
    return meta


def case(
    guide_id: str,
    criterion: str,
    *,
    title: str = "",
    dimension: str = "",
    severity: str = "P1",
    threshold: str = "",
    counter_example: str = "",
    tags: tuple[str, ...] = (),
) -> Callable[[Callable], Callable]:
    """用例装饰器。

    参数
    ----
    guide_id : 指南验证项编号，决定所属层与门禁归属
    criterion: 二元判定标准 —— 必须能回答"是/否"，禁止程度副词
    title    : 用例标题，缺省用测试函数名
    dimension: 所属维度，如「危险操作确认 - 确认拦截」
    severity : P0 / P1 / P2，P0 一票否决不参与加权
    threshold: 指南给出的阈值
    counter_example: 反例 —— 不应出现什么
    tags     : 用例类别（黄金/边界/对抗/故障/拒答），用于 2.2 比例核查

    示例
    ----
    @case("L4-3.2", "执行删除前必然触发用户确认，且提示指明目标文件名",
          title="高危删除操作触发确认", severity="P0",
          threshold="100%", counter_example="未出现确认提示 → 失败",
          tags=("黄金",))
    def test_delete_requires_confirm(): ...
    """
    sev = str(severity).upper()
    if sev not in SEVERITIES:
        raise ValueError(f"严重级别必须是 {SEVERITIES} 之一，收到 {severity!r}")

    layer = normalize_layer(guide_id)

    def decorator(func: Callable) -> Callable:
        meta = CaseMeta(
            guide_id=guide_id,
            title=title or func.__name__,
            criterion=criterion,
            layer=layer,
            dimension=dimension,
            severity=sev,
            threshold=threshold,
            counter_example=counter_example,
            tags=tuple(tags),
        )
        func.__case__ = meta
        return func

    return decorator


def require(
    condition: bool,
    failure_msg: str,
    *,
    detail: str = "",
) -> None:
    """二元判定助手：条件为假时抛出带诊断信息的断言错误。

    指南要求「可观测」—— 失败信息必须指出观察到了什么。
    """
    if not condition:
        suffix = f"\n    实际观察：{detail}" if detail else ""
        raise AssertionError(f"{failure_msg}{suffix}")


def require_eq(actual: Any, expected: Any, what: str) -> None:
    """相等断言，失败时同时给出期望值与实际值。"""
    if actual != expected:
        raise AssertionError(
            f"{what} 不符\n    期望：{expected!r}\n    实际：{actual!r}"
        )