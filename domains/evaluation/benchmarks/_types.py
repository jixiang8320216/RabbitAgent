"""domains.evaluation.benchmarks 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.evaluation.benchmarks.bfcl.evaluator import BFCLEvaluator
from domains.evaluation.benchmarks.gaia.evaluator import GAIAEvaluator
from domains.evaluation.benchmarks.data_generation.llm_judge import LLMJudgeEvaluator
from domains.evaluation.benchmarks.data_generation.win_rate import WinRateEvaluator


"""
Benchmarks 模块

包含各种智能体评估基准测试:
- BFCL: Berkeley Function Calling Leaderboard
- GAIA: General AI Assistants Benchmark
- Data Generation: 数据生成质量评估
"""
