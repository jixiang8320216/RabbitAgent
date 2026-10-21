"""domains.tool.infrastructure 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.tool.infrastructure.base import Tool, ToolParameter
from domains.tool.infrastructure.registry import ToolRegistry, global_registry
from domains.tool.builtin.search_tool import SearchTool
from domains.tool.builtin.calculator import CalculatorTool
from domains.tool.builtin.memory_tool import MemoryTool
from domains.tool.builtin.rag_tool import RAGTool
from domains.tool.builtin.note_tool import NoteTool
from domains.tool.builtin.terminal_tool import TerminalTool
from domains.tool.builtin.protocol_tools import MCPTool, A2ATool, ANPTool
from domains.tool.builtin.bfcl_evaluation_tool import BFCLEvaluationTool
from domains.tool.builtin.gaia_evaluation_tool import GAIAEvaluationTool
from domains.tool.builtin.llm_judge_tool import LLMJudgeTool
from domains.tool.builtin.win_rate_tool import WinRateTool
from domains.tool.builtin.rl_training_tool import RLTrainingTool
from domains.tool.infrastructure.chain import ToolChain, ToolChainManager, create_research_chain, create_simple_chain
from domains.tool.infrastructure.async_executor import AsyncToolExecutor, run_parallel_tools, run_batch_tool, run_parallel_tools_sync, run_batch_tool_sync


"""工具系统"""
