"""domains.tool.builtin 聚合包。

采用 PEP 562 模块级 ``__getattr__`` 做**惰性导出**。

为什么不用急切 re-export：迁移把多个源顶层包折叠进同一目标目录，急切导入会在包
初始化期间拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环
导入。惰性化后，只有真正访问某个导出名时才会求值。

为什么不用全项目 AST 扫描兜底：扫描式回退会在模块被移动后静默失效，表现为运行期
``ModuleNotFoundError``，而静态检查完全看不出问题。**显式映射让漏改在导入时立刻暴露。**
"""

# 惰性导出映射：导出名 -> 定义所在模块
_LAZY = {
    "A2ATool": "domains.tool.builtin.protocol_tools",
    "AIDataset": "domains.tool.builtin.llm_judge_tool",
    "ANPTool": "domains.tool.builtin.protocol_tools",
    "BFCLEvaluationTool": "domains.tool.builtin.bfcl_evaluation_tool",
    "CHARS_PER_TOKEN": "domains.tool.builtin.search_tool",
    "CalculatorTool": "domains.tool.builtin.calculator",
    "DDGS": "domains.tool.builtin.search_tool",
    "DEFAULT_MAX_RESULTS": "domains.tool.builtin.search_tool",
    "GAIADataset": "domains.tool.builtin.gaia_evaluation_tool",
    "GAIAEvaluationTool": "domains.tool.builtin.gaia_evaluation_tool",
    "GAIAEvaluator": "domains.tool.builtin.gaia_evaluation_tool",
    "GAIAMetrics": "domains.tool.builtin.gaia_evaluation_tool",
    "LLM": "domains.tool.builtin.llm_judge_tool",
    "LLMJudgeEvaluator": "domains.tool.builtin.llm_judge_tool",
    "LLMJudgeTool": "domains.tool.builtin.llm_judge_tool",
    "MCPTool": "domains.tool.builtin.protocol_tools",
    "MCPWrappedTool": "domains.tool.builtin.mcp_wrapper_tool",
    "MCP_SERVER_ENV_MAP": "domains.tool.builtin.protocol_tools",
    "MemoryConfig": "domains.tool.builtin.memory_tool",
    "MemoryTool": "domains.tool.builtin.memory_tool",
    "NoteTool": "domains.tool.builtin.note_tool",
    "RAGTool": "domains.tool.builtin.rag_tool",
    "RLTrainingTool": "domains.tool.builtin.rl_training_tool",
    "SUPPORTED_BACKENDS": "domains.tool.builtin.search_tool",
    "SUPPORTED_RETURN_MODES": "domains.tool.builtin.search_tool",
    "SearchTool": "domains.tool.builtin.search_tool",
    "TerminalTool": "domains.tool.builtin.terminal_tool",
    "Tool": "domains.tool.builtin.bfcl_evaluation_tool",
    "ToolParameter": "domains.tool.builtin.bfcl_evaluation_tool",
    "WinRateEvaluator": "domains.tool.builtin.win_rate_tool",
    "WinRateTool": "domains.tool.builtin.win_rate_tool",
    "calculate": "domains.tool.builtin.calculator",
    "create_reward_function": "domains.tool.builtin.rl_training_tool",
    "evaluate_model": "domains.tool.builtin.rl_training_tool",
    "load_dataset": "domains.tool.builtin.rl_training_tool",
    "search": "domains.tool.builtin.search_tool",
    "search_hybrid": "domains.tool.builtin.search_tool",
    "search_serpapi": "domains.tool.builtin.search_tool",
    "search_tavily": "domains.tool.builtin.search_tool",
    "train_with_grpo": "domains.tool.builtin.rl_training_tool",
    "train_with_sft": "domains.tool.builtin.rl_training_tool",}


def __getattr__(name):
    import importlib

    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(_LAZY)
