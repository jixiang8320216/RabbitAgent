# 业务域参考

本文档逐一说明 25 个业务域的职责、关键文件与使用方式。

统计口径：708 个 `.py` 文件（不含 `.venv` / `__pycache__` / `.idea`），约 3.4 MB。

---

## 目录

- [核心链路](#核心链路)：[agent](#agent) · [llm](#llm) · [runtime](#runtime) · [context](#context) · [configuration](#configuration)
- [能力插件](#能力插件)：[tool](#tool) · [skill](#skill) · [plugin](#plugin) · [hook](#hook) · [mcp](#mcp) · [protocol](#protocol)
- [状态与数据](#状态与数据)：[session](#session) · [memory](#memory) · [persistence](#persistence) · [workspace](#workspace) · [resource_catalog](#resource_catalog)
- [治理与观测](#治理与观测)：[permission](#permission) · [observability](#observability) · [evaluation](#evaluation) · [billing](#billing)
- [其他](#其他)：[plan](#plan) · [expert](#expert) · [rl](#rl) · [network](#network) · [events](#events)

---

# 核心链路

## agent

**122 文件 / 830 KB —— 全工程最大的域。**

agent 的领域模型、策略与编排。

| 层 | 内容 |
|---|---|
| `domain/entities/` | `agent_instance.py` 等实体 |
| `domain/aggregates/` | `react_agent.py`（ReAct 聚合根）、`chat_model.py`（`ChatModel` 抽象） |
| `domain/value_objects/` | `message.py`（`Msg`）、`runtime_context.py`、请求上下文 |
| `domain/services/` | `tool_loop_detector.py`（工具循环检测） |
| `application/` | `loader/`（root agent、sub agent 加载）、`factory/`、`toolkit/`（工具集构建）、`prompt/`（系统提示词构建）、`context/`（优化器适配）、`scheduling/`、`lifecycle/` |
| `infrastructure/` | 具体实现 |

**关键概念**：

- `ReActAgent` —— 推理-行动循环的聚合根。**872 行，建议后续拆分**
- `ChatModel.__call__` —— **异步生成器**，`async for` 拿 `ChatResponse` 流
- `RuntimeContext` —— 通过 ContextVar 安装，全域读取路径与配置

**`runtime_context.py` 字段**：`user_data_home` / `apps_dir` / `sessions_dir` / `config_dir` 等。
⚠️ 注意字段名是 `apps_dir`（不是 `app_dir`）。

**`react_agent._summarize`** —— 工具循环耗尽时的收尾回复，**不是上下文压缩**。

---

## llm

**105 文件 / 592 KB。**

provider 家族、协议适配、消息格式、token 计数。

```
domains/llm/provider/
├── domain/              provider 模型、配置、认证
├── application/         模型创建、消息格式化、token 计数、错误消息
├── infrastructure/
│   ├── families/        anthropic/ · openai_compatible/
│   ├── protocols/       openai_compatible/ 等协议适配
│   └── foundation/      chat_transport/ 等基础设施
└── composition.py       provider 运行时初始化
```

**内置 provider 家族**：`anthropic`、`openai_compatible`

> ⚠️ `AzureProvider` / `OpenAICodexProvider` 的映射指向**未迁移**的
> `families/azure_openai/`、`families/openai_codex/` —— 访问即
> `ModuleNotFoundError`（全工程 14 条坏映射均源于此）。

**Token 计数**（`application/token/`）：

- `token_counter.py` —— `TokenCounter`，按 `agent_name` × `operation_kind` 双维度累计
- `openai_token_counting.py` —— OpenAI 侧本地估算

**`TokenCounter` 关键方法**：`count` / `set_api_tokens` / `set_last_api_token` /
`get_agent_tokens` / `get_agent_last_tokens` / `get_agent_tokens_by_kind` /
`register_memory_sink` / `reset`

> `provider_chat_model.py` 用**能力探测**调用它：
> `getattr(self.token_counter, "set_api_tokens", None)` —— 缺方法时静默跳过。
> 方法缺失不会报错，但统计也不会工作。

---

## runtime

**40 文件 / 374 KB。**

应用编排层 —— 组装所有东西并驱动会话。

| 文件 | 职责 |
|---|---|
| `rabbit_application.py` | 应用主体（`RabbitApplication`） |
| `rabbit_application_composition.py` | 组合根基类 |
| `orchestrators/agent_orchestrator.py` | agent 编排（`AgentOrchestrator`） |
| `orchestrators/session_orchestrator.py` | 会话编排 |
| `orchestrators/session_lifecyle.py` | 会话生命周期（注意文件名 `lifecyle` 少一个 `c`） |
| `orchestrators/session_hook_lifecycle.py` | hook 会话收尾 |

**`RabbitApplication` 关键方法**：`init_session` / `cleanup` / `_load_agent_configs` /
`_create_agents`

**`AgentOrchestrator` 关键方法**：`cleanup_session_resources`（释放 agent 实例 +
结束 hook 会话，幂等，失败只记日志）

> ⚠️ **命名歧义**：此处的 "runtime" 指**编排运行时**，与被解散的旧 `runtime/`
> （消息模型）同名不同义。建议改名 `orchestration`。

---

## context

**8 文件 / 37 KB。**

运行时构建与上下文优化。

| 文件 | 职责 |
|---|---|
| `runtime_builder.py` | `build_runtime()` —— 核心装配入口，10 阶段 |
| `app_runtime/` | 运行时辅助 |
| `application/context_optimizer.py` | `ContextOptimizer` —— 提示词压缩 |

**`build_runtime_context()`** —— `build_runtime_context` 的向后兼容别名。
两者行为相同：构造 `RuntimeContext`、安装到 ContextVar、初始化 provider 运行时、
装配生命周期通知器。

> 复现测试**必须先 `bootstrap_context()`** —— `_resolve_session_arg` 依赖
> `RuntimeContext`。真实启动路径本身会先调，所以正式启动不受影响。

**`ContextOptimizer.optimize(prompt)`** —— 压缩系统提示词。

- 可同步可异步，返回 `str`
- **任何失败都返回空串**，调用方据此回落原文
- 压缩后不比原文长才返回（否则回落）

---

## configuration

**39 文件 / 127 KB。**

配置模型、合并、迁移、本地存储。

| 位置 | 内容 |
|---|---|
| `domain/model/` | `app_config.py`、`app_mode.py`、`llm_config.py` |
| `domain/runtime_state/` | `app_state.py`、`prompt_expansion.py` |
| `domain/protocol/` | `project_layout_impl.py` |
| `infrastructure/store/` | `user_config_store.py`、`project_config_store.py` |
| `infrastructure/merge/` | `config_merger.py` |
| `infrastructure/adapter/` | `app_mode_resolver.py` |
| `infrastructure/logging_bootstrap.py` | `LOGGER_CONFIG` |

**内置应用模式**（`BUILTIN_APP_MODES`）：

| 模式 | 说明 |
|---|---|
| `delegate` | 委托模式（默认）—— 主 agent 委托子 agent 执行任务 |
| `roleplay` | 角色扮演 —— agent 以特定角色身份交互 |
| `solo` | 单干模式 —— 单个 agent 独立完成所有任务 |
| `guarded` | 守护模式 —— 带额外安全约束的执行模式 |
| `groupchat` | 群聊模式 —— 多 agent 群体协作对话 |
| `pipeline` | 流水线模式 —— 固定顺序多阶段处理 |
| `parallel` | 并行模式 —— 独立处理后汇总 |
| `debate` | 辩证 —— 对抗性多 agent + 裁判 |

**`canonicalize_app_mode(value)`** —— `strip()` + `lower()` 归一化，非法则 `ValueError`。
放在 domain 层（模式名是领域概念），裁决逻辑在 infrastructure 层。

**用户配置加载**（`load_or_create_user_config`）：

- 文件不存在或损坏 → 写入默认配置后返回
- 顶层非对象 → 记 warning，回退默认
- 解析失败 → 记 warning，回退默认
- 返回 **`copy.deepcopy(DEFAULT_LLM_CONFIG)`** —— 深拷贝，避免污染模块级默认

---

# 能力插件

## tool

**34 文件 / 284 KB。**

工具核心与内置工具集。

```
domains/tool/
├── core/            工具基类、注册表、链式调用
├── builtin/         14 个内置工具
├── command/         命令类工具
├── lsp/             LSP 集成
├── scheduling/      工具调度
└── infrastructure/  基础设施
```

**内置工具**（`builtin/`）：

| 工具 | 用途 |
|---|---|
| `search_tool` | 网页搜索（需 `ddgs`/`tavily`/`serpapi`） |
| `terminal_tool` | 终端执行 |
| `calculator.py` | 计算 |
| `note_tool.py` | 笔记 |
| `rag_tool.py` | RAG 检索 |
| `memory_tool.py` | 记忆读写（单工具 + `action` 参数） |
| `mcp_wrapper_tool.py` | MCP 工具包装 |
| `protocol_tools.py` | A2A / ANP 协议工具 |
| `llm_judge_tool.py` | LLM 评判 |
| `win_rate_tool.py` | 胜率统计 |
| `rl_training_tool.py` | RL 训练 |
| `bfcl_evaluation_tool.py` | BFCL 评测 |
| `gaia_evaluation_tool.py` | GAIA 评测 |

**`MemoryTool` 的 action 参数**：`add` / `search` / `update` / `remove` /
`summary` / `stats` / `forget` / `consolidate` / `clear_all`

> 记忆做成**单工具**而非一组：操作种类多，拆开会挤占模型工具预算，
> 也让模型在「该用哪个」上纠结。

---

## skill

**26 文件 / 72 KB。**

skill 定义、加载、演化。

```
domains/skill/
├── domain/          skill 模型
├── application/     加载与编排
├── infrastructure/  持久化
└── service/         演化服务（skill evolution）
```

Skill 通过生命周期通知器接收心跳信号，走演化通道。

---

## plugin

**14 文件 / 49 KB。**

插件加载、贡献注册、状态管理。

| 文件 | 职责 |
|---|---|
| `application/plugin_manager.py` | `PluginManager` —— 加载/停用/查询 |
| `domain/contribution.py` | `ContributionType`、`PluginState`、贡献模型 |

**`PluginState` 状态流转**：

```
UNKNOWN → LOADED → DISABLED
           ↓
         ERROR
```

**`PluginManager.shutdown_plugins(timeout_seconds)`** —— 停用所有插件并清理钩子。
幂等，单插件失败不影响其余。

**非空模块**：`plugin_manager.py` `plugin_loading.py` `contribution.py`
`execution_guard.py` `request_context.py`

**空壳占位**（4 个）：`plugin_contracts` `plugin_loader` `ports` `tool_execution`

---

## hook

**28 文件 / 33 KB（其中 15 个非空，13 个 0 字节空壳）。**

hook 生命周期与事件分发。

**非空模块**：`hook_manager.py` `hook_runtime.py` `hook_definitions.py`
`management_models.py` `lifecycle_hook_configuration.py` `inputs.py`
`outputs.py` `resource_models.py`

**空壳占位**（13 个）：`async_delivery` `compiler` `hook_transcript` `recursion`
`resource_ports` `run_recorder` `runtime_ports` `state_service` `store_ports`
（application 层）· `env_file` `runtime_models` `store_models`（domain 层）·
`handler_execution`（infrastructure 层）

> 这些空壳是**迁移遗留的接口占位** —— 调用点存在但实现未落地。
> 访问其符号会抛 `AttributeError`。

**Hook 事件**：`SETUP` / `SLASH_COMMAND` / `MCP_PROMPT` / `PRE_REPLY` 等。

**Hook 作用域**：`MANAGED` / `SYSTEM` / `USER` / `PROJECT` / `LOCAL` / `PLUGIN` /
`SKILL` / `AGENT` / `SESSION`

**Hook 来源**：`MANAGED` / `SYSTEM` / `RABBIT` / `PLUGIN` / `SKILL` / `AGENT` / `SESSION`

**会话收尾**（`session_hook_lifecycle.end_hook_session`）—— 派发 `SessionEnd` 事件。

---

## mcp

**7 文件 / 19 KB。**

MCP（Model Context Protocol）客户端与服务端。

MCP 工具以 **tool 形式**进入工具集 —— `list_tools()` 结果投影成 tool，
与内置工具同名冲突有专门处理逻辑。

---

## protocol

**14 文件 / 65 KB。**

按协议组织：`a2a/` · `anp/` · `mcp/`

| 协议 | 用途 |
|---|---|
| A2A | Agent-to-Agent 通信（`a2a-sdk`，可选依赖） |
| ANP | Agent Network Protocol |
| MCP | Model Context Protocol |

---

# 状态与数据

## session

**11 文件 / 46 KB。**

会话持久化、迁移、事件日志。

| 文件 | 职责 |
|---|---|
| `infrastructure/session_persistence.py` | `SessionPersistence` —— 会话读写、列表 |
| `infrastructure/session_file_catalog.py` | `SessionFileCatalogQuery` —— 文件扫描 |
| `infrastructure/file_event_log.py` | `AppV3EventPersistence` |
| `migration.py` | 旧格式迁移 |

**会话文件命名** —— `SessionFileCatalogQuery` 负责识别会话目录：

- 排除非会话目录（`agent_executions` / `commands` / `hooks` / `logs` / `skills` /
  `plugins` / `tmp` / `cache`）
- UUID 形状判断（5 段十六进制）
- `recover_session_id()` —— 从损坏名（pydantic repr 泄漏）还原真实 UUID

**`SessionPersistence.list_sessions(sessions_dir, limit, include_old_format)`** —— 列出会话。

---

## memory

**31 文件 / 289 KB。**

记忆、RAG、三种存储后端。

```
domains/memory/
├── domain/          记忆模型
├── application/     memory_runtime_service.py · memory_agent_tools.py
├── infrastructure/  装配
├── rag/             RAG 管线（文档解析 → 分块 → 向量 → 检索）
├── storage/         document_store · qdrant_store · neo4j_store
└── types/           类型定义
```

**存储后端**：

| 后端 | 类 | 状态 |
|---|---|---|
| SQLite | `SQLiteDocumentStore` | ✅ 完全可用 |
| Qdrant | `QdrantVectorStore` | 客户端正常；需外部服务 |
| Neo4j | `Neo4jGraphStore` | 代码完整；需外部服务 |

> ⚠️ `qdrant_store.py` 曾 import qdrant-client 1.19 已移除的 `SearchRequest`
> （**从未使用**），导致整个 try 块失败 → 误报「未安装」→ 后端完全不可用。
> 已修复。**教训：可选依赖的 try 块只 import 实际用到的符号。**

**`register_memory_tools(toolkit, runtime)`** —— 把记忆挂进工具集。
注册失败只记日志（记忆是可选能力，不该让 agent 装配失败）。

---

## persistence

**17 文件 / 22 KB。**

持久化端口与实现。`infrastructure/file/` 下提供原子写（依赖 `fsspec`）。

Provider 由 `RABBIT_PERSISTENCE_PROVIDER` 决定：`file` / `sqlite` / `pg` / `postgres` / `postgresql`。

---

## workspace

**10 文件 / 7 KB。**

工作区、检查点、版本控制。

---

## resource_catalog

**8 文件 / 24 KB。**

本地资源发现 —— 扫描项目内的可复用资源。

---

# 治理与观测

## permission

**27 文件 / 57 KB。**

授权、确认、审批工作流、中断管理。

```
domains/permission/
├── domain/          confirmation_strategy.py（抽象基类）· policy.py
├── application/     approval_workflow.py · authorization_service.py
│                    confirmation_registry.py · interrupt_coordination.py
├── infrastructure/  confirmation_adapter.py · yaml_rule_provider.py
│                    file_approval_repository.py · hook_gateway.py
│                    security/credential_encryption.py
├── cli_strategy.py              ← CLI 侧确认策略
├── infrastructure/cli_strategy.py
└── interrupt_manager.py         ← 顶层垫层
```

**`ConfirmationStrategy` 抽象基类** —— 类级注册表：

```python
ConfirmationStrategy.register_strategy(strategy)
ConfirmationStrategy.get_active_startegy()
```

> ⚠️ 方法名沿用项目既定 typo（`startegy`），与 `tui_application.py` 的调用点保持
> 一致，不单独修正以免破坏调用方。

**`CLIConfirmationStrategy`** —— 构造时接收 `TUIObserver`，用于在需要用户确认时
展示提示并等待应答。

> ⚠️ 存在拼写分叉：`cli_strategy.py` 是 `CLIConfirmationStrategy`（正确，实际使用中），
> `infrastructure/cli_strategy.py` 曾是 `CLIConfirmationStartegy`（typo，内容重复）。
> 已统一为正确拼写 + 保留 typo 别名。

**`InterruptManager`** —— 确认/中断协调。

---

## observability

**34 文件 / 111 KB。**

遥测、observer、OTel 导出、健康监控。

| 位置 | 内容 |
|---|---|
| `telemetry_config.py` | `TelemetryConfig` —— `from_runtime_config` / `to_policy` / `to_opentelemetry` |
| `application/telemetry_runtime.py` | sink 注册表（`replace_sinks` / `reset_sinks`） |
| `application/health.py` | `HealthMonitor` —— 队列水位、端点可达性 |
| `application/observability_context.py` | 进程级状态（entry_point / owner / zone） |
| `application/guard.py` | 慢调用告警（`SLOW_CALL_THRESHOLD`） |
| `infrastructure/opentelemetry/` | 导出配置、处理器链、TLS 处理、runlog 落盘 |
| `infrastructure/identity/` | 进程属主探测 |
| `infrastructure/network/` | 网络区域探测 |

**告警类别**（`TelemetryAlertCategory`）：

- `ENDPOINT_UNREACHABLE` —— 远程端点不可达
- `QUEUE_NEAR_CAPACITY` —— 队列接近容量上限（≥80%）
- `QUEUE_OVERFLOWED` —— 队列已溢出
- `INTERNAL_ERROR` —— 采集端内部异常

**`RunlogLogExporter`** —— 本地 JSON Lines 兜底。远程端点不可达时事件仍有地方可查。
追加写，崩溃时最多丢最后一行。

> ⚠️ 当前 opentelemetry SDK 版本缺 `LoggingProvider` → 遥测降级为仅本地 runlog。

---

## evaluation

**20 文件 / 120 KB。**

基准评测：`benchmarks/` 下含 BFCL / GAIA / data_generation。

---

## billing

**5 文件 / 20 KB。**

成本预算与计费（基于 LiteLLM 定价）。

| 文件 | 内容 |
|---|---|
| `domain/litellm_budget.py` | `BudgetTracker` / `BudgetState` / `BudgetScope` / `BudgetExceededAction` / `estimate_cost` |
| `infrastructure/litellm_client.py` | `LiteLLMClient` / `LiteLLMUsage` |

**预算维度**（`BudgetScope`）：`GLOBAL` / `AGENT` / `SESSION`

> 多 Agent 场景下，总预算超了不代表有问题 —— 可能只是某个子 Agent 失控。
> 分维度统计才能定位到具体环节。

**超限策略**（`BudgetExceededAction`）：`WARN`（默认，记告警继续）/ `REJECT` / `RAISE`

> ⚠️ LiteLLM 为可选依赖，未安装时 `estimate_cost` 返回 `None`，降级为不计费但继续。
> ⚠️ `billing` 域**已可用但未接入主链路** —— 还没有调用方把 `TokenCounter` 的
> 用量喂给 `BudgetTracker`。

---

# 其他

## plan

**6 文件 / 13 KB。** 计划执行。

## expert

**6 文件 / 27 KB。** 专家包。

## rl

**10 文件 / 46 KB。** 强化学习：`dataset/` `protocol/` `reward/` `training/`（依赖 `datasets`）。

## network

**3 文件 / 5 KB。** 网络配置。

## events

**6 文件 / 8 KB。** 事件定义。
