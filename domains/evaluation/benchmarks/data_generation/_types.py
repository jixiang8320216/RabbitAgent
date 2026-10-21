"""domains.evaluation.benchmarks.data_generation 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.evaluation.benchmarks.data_generation.dataset import AIDataset
from domains.evaluation.benchmarks.data_generation.llm_judge import LLMJudgeEvaluator
from domains.evaluation.benchmarks.data_generation.win_rate import WinRateEvaluator


"""
Data Generation Evaluation Module

评估数据生成质量的模块，包括：
- LLM Judge: 使用LLM作为评委评估生成质量
- Win Rate: 通过对比评估计算胜率
"""
