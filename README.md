# RabbitAgent

灵活、可扩展的多智能体框架 —— DDD 分层版本。

**708 个 Python 源文件**（约 3.4 MB），按**业务能力域**重组为三层架构，
所有 import 均为带完整前缀的绝对形式（`domains.*` / `interfaces.*` / `shared.*`）。

```
┌──────────────────────────────────────────────────────────┐
│  interfaces/     外部协议适配：TUI / CLI / Commands / ACP │
├──────────────────────────────────────────────────────────┤
│  domains/        25 个业务域，各自含 domain / application │
│                  / infrastructure 三层                    │
├──────────────────────────────────────────────────────────┤
│  shared/         跨域技术设施：路径 / 异常 / 网络 / 配置   │
└──────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 环境准备

```bash
# Windows
rabbit-setup.bat

# macOS / Linux
./rabbit-setup.sh
```

脚本会自动创建 `.venv` 并安装 `requirements.txt` 中的依赖。

### 运行

```bash
# Windows
rabbit-cli.bat

# macOS / Linux
./rabbit-cli.sh

# 或直接运行（需已激活虚拟环境）
python main.py
```

启动后进入 **Textual 交互式 TUI**。直接输入自然语言即可对话，
输入 `/` 可唤出命令补全（26 条内置命令）。

### 恢复历史会话

```bash
python main.py --session 3c59a114          # 8 位前缀即可
python main.py --session 3c59a114-6c43-4124-a3e6-7b62d7dbfea1   # 完整 ID
```

前缀匹配会扫描本地会话库；匹配不到会给出提示并退回新建会话。

### 首次使用前：配置模型

LLM 配置**不走环境变量**，通过用户配置文件或设置页管理：

- 配置文件位置：`~/.rabbit/config.json`（结构见 `resources/conf/config.json`）
- 或在 TUI 内执行 `/config` 命令

必需字段是 `llmConfigs` 数组（provider / API URL / model / key）。

---

## 架构总览

### 依赖方向

```
interfaces  ──►  application  ──►  domain
                     ▲
                     │
            infrastructure（实现 application 声明的 ports）
```

**硬约束**（违反即为设计错误）：

- ❌ `domain` → `application` / `infrastructure` / 第三方 SDK
- ❌ `domains/A/**` → `domains/B/infrastructure/**`（跨域只走对方的 domain 或 application）
- ❌ `domains/**` → `interfaces/**`

### 25 个业务域

| 域 | 文件 | 职责 |
|---|---:|---|
| `agent` | 122 | agent 策略、编排、实例模型、ReAct 聚合根 |
| `llm` | 105 | provider 家族、协议适配、消息格式、token 计数 |
| `runtime` | 40 | 应用编排层（`rabbit_application` + orchestrators） |
| `configuration` | 39 | 配置模型、合并、迁移、本地存储 |
| `tool` | 34 | 工具核心 + 13 个内置工具 + LSP + 调度 |
| `observability` | 34 | 遥测、observer、OTel 导出、健康监控 |
| `memory` | 31 | 记忆、RAG、三种存储后端（SQLite/Qdrant/Neo4j） |
| `hook` | 28 | hook 生命周期、事件分发 |
| `permission` | 27 | 授权、确认、审批工作流、中断管理 |
| `skill` | 26 | skill 定义、加载、演化 |
| `evaluation` | 20 | 基准评测（BFCL / GAIA / data_generation） |
| `persistence` | 17 | 持久化端口与文件/状态实现 |
| `protocol` | 14 | A2A / ANP / MCP 协议实现 |
| `plugin` | 14 | 插件加载、贡献注册、状态管理 |
| `session` | 11 | 会话持久化、迁移、事件日志 |
| `rl` | 10 | 强化学习：数据集、奖励、训练、协议 |
| `workspace` | 10 | 工作区、检查点、版本控制 |
| `context` | 8 | 运行时构建、上下文优化（提示词压缩） |
| `resource_catalog` | 8 | 本地资源发现 |
| `mcp` | 7 | MCP 客户端 / 服务端 |
| `plan` | 6 | 计划执行 |
| `expert` | 6 | 专家包 |
| `events` | 6 | 事件定义 |
| `billing` | 5 | 成本预算与计费（基于 LiteLLM 定价） |
| `network` | 3 | 网络配置 |

**详细说明见 [`docs/DOMAINS.md`](docs/DOMAINS.md)。**

### 运行时装配

入口 `main.py` → `build_runtime()`（`domains/context/runtime_builder.py`），10 个阶段：

1. 构建 `RuntimeDescriptor`（配置合并 + LLM 解析 + MCP 装配）
2. 准备运行时配置数据并落盘
3. 装配周边资源（插件加载、记忆文档迁移）
4. 异步构建 system resources
5. 构建 `RuntimeContext`（注入 ContextVar）
6. 探测 bundled runtime prompt
7. 构建 `AppConfig`
8. 初始化 provider 运行时
9. 安装生命周期通知器
10. 返回 `ACPRuntimeBuild`

---

## 目录结构

```
RabbitAgent/
├── domains/           25 个业务域（domain / application / infrastructure）
├── interfaces/        外部适配：tui / cli / commands / acp
├── shared/            跨域技术设施（paths / exceptions / network / technical_config）
├── resources/conf/    打包配置（config.json / logging.ini）
├── docs/              本文档套件
├── main.py            入口
├── init.py            顶层兼容垫层（旧版公开导出名的惰性转发）
├── version.py         版本信息
├── e2e_tui_test.py    TUI 端到端验证脚本（11 项检查）
└── requirements.txt
```

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 分层规则、域内结构、装配链路、惰性导出机制 |
| [`docs/DOMAINS.md`](docs/DOMAINS.md) | 25 个业务域逐一说明与关键文件索引 |
| [`docs/CLI.md`](docs/CLI.md) | 26 条命令详解、会话恢复、配置 |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | 环境搭建、验证流程、常见坑、遗留问题 |

---

## 依赖

**硬依赖**（缺任一项则 import 即失败）：`shortuuid` `dashscope` `fsspec`
`transformers` `datasets` `fastmcp` `huggingface-hub`

**可选依赖**（代码内已做 try/except 降级）：
`ddgs` `markdownify` `tavily-python` `serpapi`（搜索工具）、
`qdrant-client` `neo4j` `Pillow` `scikit-learn` `markitdown`（记忆 / RAG）、
`a2a-sdk` `Flask`（A2A 协议服务）

详见 `requirements.txt`（其中已用注释区分两类）。

---

## 验证

```bash
# 1) pytest 套件（三段式检索 / 8 种模式编排 / 历史压缩 / 架构守卫，共 45 例）
python -m pytest

# 2) 架构约束检查（三条依赖硬规则，违规即退出码 1，可接 CI）
python tools/check_architecture.py
python tools/check_architecture.py --json

# 3) TUI 端到端验证（11 项：装配 / 会话 / 渲染 / 真实模型问答）
python e2e_tui_test.py

# 4) 全模块导入检查
python -c "
import ast, pathlib
for p in pathlib.Path('.').rglob('*.py'):
    if any(x in {'.venv','__pycache__','.idea'} for x in p.parts): continue
    ast.parse(p.read_text(encoding='utf-8-sig'))
print('语法 OK')"
```

---

## 本轮补齐的能力

| 能力 | 之前 | 现在 | 代码落点 |
|---|---|---|---|
| **RAG 三段式检索** | 只跑到查询扩展 + 向量召回；`rerank_*` / `compress_*` / `expand_*` / `tldr_*` / `merge_snippets_grouped` **全项目零调用方** | 三段真正接起来：召回池 → 重排（交叉编码器，不可用时显式回落图信号融合）→ 邻居扩展 → 同文档片段合并 + 引用；每段去向写进 `report` | `domains/memory/rag/pipeline.py` |
| **8 种模式差异化编排** | `guarded/pipeline/parallel/debate` 之间没有真实的行为差异，唯一能体现差别的是 `parallel_tool_calls` 这个单次请求开关 | 每种模式一条**可执行**计划：串行传染链 / 并发扇出 / 正反轮辩 / 规划-审批-执行，并据此精化槽位可见性 | `domains/runtime/application/orchestrators/mode_orchestration.py` |
| **历史消息自动压缩** | `compressionThreshold` 无消费方、`AgentCompressionState` 是 0 字节空壳，压缩只作用于系统提示词 | 有了执行者：token 超阈值即压缩，**不切开工具调用序列**，模型摘要不可用则降级规则式，压不动就不压 | `domains/context/{domain,application}/history_compression*.py` |
| **架构约束强制** | 三条硬规则靠 review | `tools/check_architecture.py` 可失败、可 `--json`、可进 CI；当前代码库 **0 违规** | `tools/check_architecture.py` |
| **自动化测试** | 仅 `e2e_tui_test.py` | `tests/` + `pytest.ini`，45 例覆盖上面四项 | `tests/`、`pytest.ini` |
| **惰性导出坏映射** | 全项目 45 条「登记了但访问才炸」 | 重指向/补齐/摘除后 **0 条** | 各 `domains/*/__init__.py` |
| **计费链路** | `BudgetTracker` 无数据来源，billing 域是孤岛 | `TokenCounter` 每次真实用量经 `BillingUsageSink` 推给预算跟踪器；定价表不可用时不计费但继续 | `domains/billing/application/token_usage_bridge.py` |
| **可选依赖声明** | `litellm` 等未声明，能力静默降级 | 在 `requirements.txt` 里单列「可选依赖」段并写明各自降级语义 | `requirements.txt` |

---

## 已知遗留

| 问题 | 说明 |
|---|---|
| Azure / OpenAI Codex provider 未实现 | 原 `AzureProvider` 等 4 条指向未迁移模块的坏映射**已摘除**；真正要支持时按 `openai_compatible` 同族形态实现后再登记 |
| 44 个 0 字节空壳 | 集中在 `agent`（9）、`hook`（13）、`runtime`（8）、`plugin`（4）等域。多为迁移遗留的接口占位，需按调用契约逐个补齐或删除 |
| `domains/runtime` 命名歧义 | 此处 "runtime" 指编排运行时，与被解散的旧 `runtime/`（消息模型）同名不同义 |
| `domains/tool` 顶层散落 | `registry.py` `base.py` `chain.py` 未再分三层 |
| `domains/permission` 部分文件双份 | `cli_strategy.py` / `interrupt_manager.py` 在根目录与 `infrastructure/` 下各有一份 |
| 可选依赖未安装时模块不可导入 | `domains/protocol/mcp/server.py`（需 `fastmcp`）、`domains/rl/dataset/dataset_builder.py`（需 `datasets`）——惰性导出会把原因带出来，不是坏映射 |
| Qdrant 服务端未启动 | `QdrantVectorStore` 默认连 `localhost:6333`。客户端本身（含内存模式）已验证可用，但真实服务需外部部署；连不上时写入/检索会抛错而非静默 |

### 已修复的两个静默失效（本次验证发现）

| 缺陷 | 表现 | 修法 |
|---|---|---|
| 惰性导出名与子模块同名 | `file_lock` / `project_layout` 既是导出名又是子模块名；子模块一旦被导入就遮蔽 `__getattr__`，调用方拿到模块而非函数，**进程启动即崩**（`TypeError: 'module' object is not callable`） | 实现文件改名为 `file_lock_impl.py` / `project_layout_impl.py` 消除同名；新增架构规则 4 拦截此类新增 |
| Qdrant 客户端 API 变更 | `qdrant-client` 1.19 移除了 `client.search()`，而代码无条件调用它，异常被 `except` 吞掉后返回空列表——**RAG 检索静默返回 0 条**，不报错、无外显日志 | 按能力选择 `query_points()`（新版）/`search()`（旧版），并把 swallow 处改为 `logger.exception` 留栈 |
