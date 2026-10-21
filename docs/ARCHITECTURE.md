# 架构说明

本文档描述 RabbitAgent 的分层规则、域内结构、装配链路与核心机制。

---

## 一、三层架构

### 1.1 依赖方向

```
┌─────────────────────────────────────────────────────────┐
│  interfaces/    外部协议适配层                            │
│                 cli · tui · commands · acp               │
│                 只依赖 domains，不被任何域依赖             │
├─────────────────────────────────────────────────────────┤
│  domains/       业务域层                                 │
│  ┌───────────────────────────────────────────────────┐  │
│  │ application/     用例编排，声明 ports              │  │
│  │ infrastructure/  实现 ports，可依赖外部 SDK        │  │
│  │ domain/          实体 / 值对象 / 领域服务          │  │
│  │                  零框架依赖，零 IO                 │  │
│  └───────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│  shared/        跨域技术设施                             │
│                 paths · exceptions · network · config    │
└─────────────────────────────────────────────────────────┘
```

### 1.2 三条硬约束

违反以下任一条即为设计错误：

| # | 约束 | 原因 |
|---|---|---|
| 1 | `domain` 不得依赖 `application` / `infrastructure` / 第三方 SDK | 领域模型必须能在无框架环境下被测试与复用 |
| 2 | `domains/A/**` 不得依赖 `domains/B/infrastructure/**` | 跨域协作只走对方的 `domain` 或 `application`；直接依赖对方基础设施会造成双向耦合 |
| 3 | `domains/**` 不得依赖 `interfaces/**` | 业务域不能感知外层协议（TUI / CLI / ACP） |

> 这三条目前**靠约定维持**，尚未落地为 import-linter 规则。

### 1.3 `shared/` 的定位

`shared/` 存放**无业务语义**的纯技术设施：

| 文件 | 职责 |
|---|---|
| `paths.py` | 用户数据目录、会话目录、日志目录的解析（`~/.rabbit/...`） |
| `exceptions.py` | 跨域共享的异常类型 |
| `network/http_client_compatibility.py` | HTTP 客户端版本兼容层 |
| `network/proxy_environment.py` | 代理环境变量构建（`ProxyEnvBuilder`） |
| `technical_config.py` | 技术配置项（数据层配置） |

**判定标准**：如果一个东西可以被原样复制到另一个项目而无需修改，它属于 `shared/`；
如果它承载了业务概念，它属于某个 `domain`。

---

## 二、域内结构

### 2.1 标准三层

大多数业务域遵循：

```
domains/<name>/
├── __init__.py            聚合包（PEP 562 惰性导出）
├── domain/                实体、值对象、领域服务、领域事件
├── application/           用例编排、ports 定义、DTO
└── infrastructure/        ports 的具体实现
```

### 2.2 非标准形态

并非所有域都严格三层：

| 域 | 实际结构 | 说明 |
|---|---|---|
| `llm` | `provider/` | provider 家族单层，内部再分 `domain`/`application`/`infrastructure`/`composition` |
| `tool` | `core/` `builtin/` `command/` `lsp/` `scheduling/` `infrastructure/` | 按**能力**而非按分层组织 |
| `memory` | `domain/` `application/` `infrastructure/` `rag/` `storage/` `types/` | `rag`/`storage` 是横切能力 |
| `protocol` | `a2a/` `anp/` `mcp/` | 按**协议**组织 |
| `rl` | `dataset/` `protocol/` `reward/` `training/` | 按**研究流程**组织 |
| `runtime` | `application/` | 仅应用编排层 |
| `context` | `application/` `app_runtime/` | 运行时构建 |
| `plan` | `application/` `domain/` | 缺 infrastructure |
| `events` | `application/` `domain/` | 缺 infrastructure |
| `network` | `domain/` | 仅领域模型 |
| `resource_catalog` | `infrastructure/` | 仅基础设施 |
| `evaluation` | `benchmarks/` | 按评测套件组织 |

**这些偏离是历史重组的产物**，不是设计意图。新增能力时优先遵循标准三层。

---

## 三、聚合包与惰性导出

### 3.1 为什么用 PEP 562

所有 `domains/*/__init__.py` 与 `interfaces/*/__init__.py` 都用模块级 `__getattr__`
做**惰性导出**：

```python
_LAZY = {
    "AgentExecutorThread": "interfaces.cli.agent_executor",
    "TUIApplication": "interfaces.cli.tui_application",
    ...
}

def __getattr__(name):
    import importlib
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value
```

**动机**：迁移把多个源顶层包折叠进同一目标目录。急切 re-export 会在包初始化期间
拉起整条子模块链（含 pydantic 模型定义），与反向依赖本包的模块构成循环导入。
惰性化后，只有真正访问某个导出名时才求值。

### 3.2 显式映射优于扫描式回退

不使用「扫全项目 AST 找符号所在模块」的自动回退。理由：

- 扫描式回退在模块被移动后**静默失效**，表现为运行期 `ModuleNotFoundError`，
  而静态检查完全看不出问题
- 显式映射让漏改在**导入时立刻暴露**

### 3.3 维护约定

新增公开符号时，必须在对应 `__init__.py` 的 `_LAZY` 中登记。

⚠️ **合并包后必须重跑该包的导出全量验证** —— 合并会让「透传型」映射
（指向别处的模块）失效，而这类失效在导入期不报错，只在访问属性时才炸：

```python
import interfaces.cli as c
bad = []
for n in c.__all__:
    try: getattr(c, n)
    except Exception as e: bad.append((n, e))
print(f"{len(c.__all__)} 个导出, 失败 {len(bad)} 个")
```

---

## 四、运行时装配

### 4.1 入口链路

```
main.py
  └─ main() → asyncio.run(asyncio_main())
       ├─ _cli_log_filter()                    控制台日志降噪
       ├─ build_argument_parser().parse_args()
       ├─ load_or_create_user_config()          ~/.rabbit/config.json
       ├─ app_mode_resolver.load_app_mode()     模式裁决
       ├─ build_runtime_context()               安装 RuntimeContext
       ├─ _resolve_session_arg(args.session)    会话恢复（前缀匹配）
       ├─ build_runtime(...)                     ★ 核心装配
       ├─ initialize_telemetry("cli")           遥测初始化
       └─ run_app(app_config, project_home)     → TUIApplication.run()
```

### 4.2 `build_runtime` 的十个阶段

`domains/context/runtime_builder.py`：

| 阶段 | 内容 |
|---|---|
| 1 | 构建 `RuntimeDescriptor` —— 配置合并 + LLM 解析 + MCP 装配，产出轻量 `ACPRuntimeDescriptor`（无副作用） |
| 2 | 准备运行时配置数据并落盘 |
| 3 | 装配周边资源（插件加载、记忆文档迁移） |
| 4 | 异步构建 system resources |
| 5 | 构建 `RuntimeContext`（注入 ContextVar，供全域读取路径） |
| 6 | 探测 bundled runtime prompt（缺失只 warning，不阻断） |
| 7 | 构建 `AppConfig`（核心装配产物） |
| 8 | 初始化 provider 运行时 |
| 9 | 安装生命周期通知器（供 skill evolution 心跳通道） |
| 10 | 返回 `ACPRuntimeBuild`（异常时清理临时文件） |

### 4.3 LLM 配置的裁决优先级

`build_runtime` 内部按四级优选级解析实际使用的模型：

```
ACP 覆盖  >  显式选择  >  激活 profile 自动选  >  legacy 兜底
```

### 4.4 RuntimeContext

`domains/agent/domain/value_objects/runtime_context.py` 定义，通过 ContextVar 安装。
core 层通过 `get_runtime_context()` 获取路径与配置，**不做本地文件发现**
（这是工程明确的红线之一）。

---

## 五、TUI 交互链路

```
main.py
  └─ interfaces.cli.tui_application.TUIApplication
       ├─ _setup_signal_handlers()      Ctrl+C 计数与强制退出
       ├─ _start_agent_executor()       后台线程 + 独立 event loop
       ├─ bind_app_request_context()     绑定 app_id / project_home
       ├─ _load_agent_configs()         加载 root + sub agent 配置
       ├─ _create_agents()              创建 agent 实例
       ├─ init_session()                初始化会话（持久化 + hook 通知）
       └─ main_loop()
            ├─ observer.get_user_input()     ← 输入
            ├─ _process_submission(user_input)
            └─ agent_executor.submit_async(coro)   跨线程提交
```

### 5.1 为什么需要独立线程

Textual 自身占用主线程的事件循环，而 agent 的 LLM 调用是长耗时协程。
若直接在主循环 await，界面会在等待期间失去响应。
`AgentExecutorThread` 起一个后台线程 + 独立 event loop，主循环通过
`submit_async` 跨线程提交任务并等待结果。

### 5.2 代理环境注入

线程启动时读运行时配置，把代理设置注入到进程环境变量
（`_inject_proxy_env_from_runtime_config`）—— 子线程内的 HTTP 客户端需要继承
这些环境变量才能走代理。

---

## 六、关键横切机制

### 6.1 能力以 tool 形式承载

| 能力 | 形式 | 说明 |
|---|---|---|
| **MCP** | tool | MCP 客户端的 `list_tools()` 结果投影成 tool，与内置工具同名冲突有专门处理 |
| **记忆存储** | tool | `MemoryTool`（单工具 + `action` 参数路由：`add`/`search`/`update`/`remove`/`summary`/`stats`/`forget`/`consolidate`/`clear_all`） |
| **上下文压缩** | **非 tool** | 装配期能力，`SystemPromptBuilder` 构造提示词时调用，不占模型工具预算 |

### 6.2 记忆三后端

| 后端 | 状态 |
|---|---|
| `SQLiteDocumentStore` | 完全可用（增/查/搜/改/统计/删） |
| `QdrantVectorStore` | 客户端正常；真实 HTTP 路径需外部服务 |
| `Neo4jGraphStore` | 代码完整（9 方法）；需外部服务 |

> LiteLLM（`billing` 域）为可选依赖，未安装时 `estimate_cost` 返回 `None`，
> 降级为「不计费但继续」。

### 6.3 上下文压缩的边界

当前压缩作用于**系统提示词**（用小模型改写得更紧凑），
**不是历史消息压缩**。历史消息的自动压缩目前有状态标记与配置项
（`compressionThreshold`），但无执行者。

---

## 七、持久化与会话

### 7.1 存储位置

| 内容 | 路径 |
|---|---|
| 用户配置 | `~/.rabbit/config.json` |
| 会话数据 | `~/.rabbit/apps/`（`.jsonl` 文件 / 会话目录） |
| 日志 | `~/.rabbit/logs/rabbit.log` |
| 内存数据库 | `~/.rabbit/rabbit.db`（`RABBIT_PERSISTENCE_SQLITE_PATH`） |

### 7.2 会话 ID 的历史脏数据

磁盘上可能存在形如
`__value____<uuid>____has_result___True_.jsonl` 的文件名 ——
pydantic 的 repr 泄漏到文件名所致（历史 bug 的产物）。

`session_file_catalog.recover_session_id()` 会从损坏名中还原真实 UUID，
同时保留 `raw_file_name` 便于溯源。

### 7.3 持久化 provider

由 `RABBIT_PERSISTENCE_PROVIDER` 决定：`file`（默认）/ `sqlite` / `pg` / `postgres` / `postgresql`。
