"""domains.evaluation.benchmarks.bfcl 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.evaluation.benchmarks.bfcl.dataset import BFCLDataset
from domains.evaluation.benchmarks.bfcl.evaluator import BFCLEvaluator
from domains.evaluation.benchmarks.bfcl.metrics import BFCLMetrics
from domains.evaluation.benchmarks.bfcl.bfcl_integration import BFCLIntegration


"""
BFCL (Berkeley Function Calling Leaderboard) 评估模块

Berkeley Function Calling Leaderboard 是评估大语言模型工具调用能力的权威基准。

主要功能:
- 数据集加载和处理
- 工具调用准确性评估
- 多种调用模式评估(简单、多函数、并行、无关检测)

参考:
- 论文: https://arxiv.org/abs/2408.xxxxx
- 排行榜: https://gorilla.cs.berkeley.edu/leaderboard.html
- 数据集: https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard
"""
