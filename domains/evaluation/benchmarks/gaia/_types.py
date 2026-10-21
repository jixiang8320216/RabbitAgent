"""domains.evaluation.benchmarks.gaia 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.evaluation.benchmarks.gaia.dataset import GAIADataset
from domains.evaluation.benchmarks.gaia.evaluator import GAIAEvaluator
from domains.evaluation.benchmarks.gaia.metrics import GAIAMetrics


"""
GAIA (General AI Assistants) 评估模块

GAIA 是由 Meta 开发的通用AI助手评估基准,包含466个真实世界问题。

主要功能:
- 数据集加载和处理
- 多级别难度评估(Level 1-3)
- 通用能力评估

参考:
- 论文: https://arxiv.org/abs/2311.12983
- 排行榜: https://huggingface.co/spaces/gaia-benchmark/leaderboard
- 数据集: https://huggingface.co/datasets/gaia-benchmark/GAIA
"""
