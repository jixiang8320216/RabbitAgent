# -*- coding: utf-8 -*-
"""RabbitAgent 智能体验证测试框架。

按《Agent 智能体验证指南》构建的验证基础设施：
- case()      用例元数据装饰器（编号 / 维度 / 二元判定标准 / 反例 / 严重级别）
- scoring     L0-L5 加权评分与发布门禁（第 5 章）
- reporter    JSON / Markdown / HTML / CSV 四种结果输出

设计约束（对应指南附录 B「判定标准书写规范」）：
1. 二元化 —— 每条用例的判定标准必须能回答"是/否"
2. 可观测 —— 判定依据来自返回值、日志、文件或异常
3. 含否定项 —— criterion 与 counter_example 同时给出
4. 绑定阈值 —— 数字优于形容词
"""

from framework.case import (
    REGISTRY,
    CaseMeta,
    LAYER_WEIGHTS,
    case,
    collect_meta,
    normalize_layer,
)
from framework.scoring import evaluate, gate_decision
from framework.reporter import write_all

__all__ = [
    "REGISTRY",
    "CaseMeta",
    "LAYER_WEIGHTS",
    "case",
    "collect_meta",
    "normalize_layer",
    "evaluate",
    "gate_decision",
    "write_all",
]