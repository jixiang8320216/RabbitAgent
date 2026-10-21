# CLI 使用手册

RabbitAgent 的命令行入口是 Textual 交互式 TUI。

---

## 启动

```bash
# Windows
rabbit-cli.bat

# macOS / Linux
./rabbit-cli.sh

# 直接运行（需已激活虚拟环境）
python main.py
```

启动脚本会自动向上查找含 `.venv` 的项目目录，激活虚拟环境，
设置 `RABBIT_PROJECT_DIR` 与 `PYTHONPATH`，并强制 `PYTHONIOENCODING=utf-8`。
退出码透传。

**Windows 首次使用**先跑 `rabbit-setup.bat`（创建 `.venv` + 安装依赖）。

---

## 基本交互

| 操作 | 说明 |
|---|---|
| 直接输入 | 自然语言对话，回车发送 |
| `/` | 唤出命令补全 |
| `Ctrl+C` | 中断当前任务（连按两次强制退出） |
| `ESC` | 中断正在执行的 agent turn |
| `Ctrl+D` | 退出 |

**界面构成**：顶部状态栏（模型名 / 上下文窗口占用），中部聊天区，底部输入框。

状态栏示例：
```
Model:deepseek-flash
Context Window: 615/128000 [                    ] 0.480469%
```

---

## 命令清单

26 条内置命令：

| 命令 | 用途 |
|---|---|
| `/help` | 帮助 |
| `/exit` | 退出 |
| `/clear` | 清屏 |
| `/status` | 状态 |
| `/agents` | agent 列表 |
| `/skills` | skill 列表 |
| `/config` | 配置 |
| `/settings` | 查看 / 编辑设置（当前为占位实现，调用不产生实际效果） |
| `/configencrypt` | 加密配置项 |
| `/configdecrypt` | 解密配置项 |
| `/web` | Web 相关 |
| `/model` | 切换模型 |
| `/theme` | 切换主题 |
| `/memory` | 记忆管理 |
| `/mcp` | MCP 服务器管理 |
| `/session` | 会话管理 |
| `/tokens` | token 用量统计 |
| `/mode` | 切换应用模式 |
| `/plan` | 计划执行 |
| `/reset` | 重置 |
| `/shell` | 执行 shell 命令 |
| `/btw` | 旁路提问 |
| `/hook` | hook 管理 |
| `/lsp` | LSP 管理 |
| `/interrupt` | 中断管理 |
| `/multiline` | 多行输入 |

### Markdown 命令扩展

除内置命令外，`CommandRegistry` 会扫描项目内的 `.md` 文件自动注册命令：

```
<项目目录>/.rabbit/commands/**/*.md
```

文件名去掉 `.md` 后缀，路径段用 `:` 连接 —— `git/commit.md` → `/git:commit`。

**Markdown 命令的内容会被渲染为 TUI 面板**（`MarkdownCommand`）。

---

## 应用模式

`/mode` 切换，共 8 种：

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

模式来自用户配置或环境变量，非法值会报 `ValueError`。

---

## 会话恢复

```bash
python main.py --session 3c59a114
python main.py --session 3c59a114-6c43-4124-a3e6-7b62d7dbfea1
```

**前缀匹配** —— 扫描本地会话库，8 位前缀即可命中。
匹配不到会打印提示并退回新建会话（`session_id=None`, `resume=False`）。

| 场景 | `session_id` | `resume` |
|---|---|---|
| 不传参 | `None` | `False` |
| 完整 ID | 同入参 | `True` |
| 8 位前缀 | 解析到完整 UUID | `True` |
| 不存在的 ID | `None` + 提示 | `False` |

**会话存储位置**：`~/.rabbit/apps/`

---

## 配置文件

### 用户配置（主配置）

**位置**：`~/.rabbit/config.json`

LLM 配置**不走环境变量**，通过这里或 TUI 的 `/config` 页管理。

必需结构 —— `llmConfigs` 数组（每项含 provider / API URL / model / key）：

```json
{
  "llmConfigs": [
    {
      "provider": "deepseek",
      "baseUrl": "https://api.deepseek.com",
      "model": "deepseek-flash",
      "apiKey": "sk-...",
      "active": true
    }
  ]
}
```

配置缺失或损坏时会自动创建默认配置文件（`load_or_create_user_config`）。

### 打包默认配置

**位置**：`resources/conf/config.json`

| 键 | 类型 | 说明 |
|---|---|---|
| `max_tokens` | int | 单次响应最大 token |
| `model` | str | 默认模型 |
| `stream` | bool | 是否流式 |
| `tempareture` | float | 温度（⚠️ 拼写如此） |
| `interactive_mode` | str | 交互模式 |
| `mcp_servers` | list | MCP 服务器配置 |
| `plugins` | dict | 插件配置（`config.take_artifact.enabled`） |
| `toolDescriptionMaxBytes` | int | 工具描述截断上限 |
| `tool_args_compression_limit` | int | 工具参数压缩上限 |
| `demo_mode` | bool | 演示模式 |
| `compressionThreshold` | int | 压缩阈值（⚠️ 当前无消费方） |
| `participant_model_refs` | dict | 参与方模型引用 |
| `note` | str | 备注 |

### 环境变量

复制 `.env.example` 为 `.env` 后按需设置：

| 变量 | 说明 |
|---|---|
| `USER_PROJECT_HOME` | 项目工作目录 |
| `NO_PROXY` | 不走代理的地址（内网必需） |
| `RABBIT_PERSISTENCE_PROVIDER` | `file`（默认）/ `sqlite` / `pg` / `postgres` / `postgresql` |
| `RABBIT_PERSISTENCE_SQLITE_PATH` | SQLite 路径（provider=sqlite 时必需） |
| `RABBIT_PERSISTENCE_DSN` | PostgreSQL DSN |
| `RABBIT_PERSISTENCE_BASE_DIR` | file provider 的基础目录 |
| `RABBIT_PROJECT_DIR` | 启动脚本设置 |

**LLM 与长期记忆不走环境变量** —— 前者见用户配置，后者由 skill / Connector 平台管理。

---

## 数据目录

| 内容 | 路径 |
|---|---|
| 用户配置 | `~/.rabbit/config.json` |
| 会话数据 | `~/.rabbit/apps/` |
| 日志 | `~/.rabbit/logs/rabbit.log` |
| 内存数据库 | `~/.rabbit/rabbit.db` |

---

## 常见问题

### 启动报 `No active session`

会话未初始化。真实启动路径会先调 `init_session()`；
若在脚本里复现，需手动走完序列：

```python
tui._setup_signal_handlers()
tui._start_agent_executor()
bind_app_request_context(app_id=session_id, project_home=..., app_instance=tui.rabbit_app)
sub_cfgs, plan_cfg = tui.rabbit_app._load_agent_configs()
await tui._submit_agent_coro(tui.rabbit_app._create_agents(sub_cfgs, plan_cfg))
real_sid = await tui._submit_agent_coro(tui.rabbit_app.init_session(session_id=..., is_resume=False))
```

### 脚本报 `AttributeError: module has no attribute 'list_sessions'`

`SessionPersistence` 必须**按类**导入：

```python
# ✗ 错：把模块别名成了类名
from domains.session.infrastructure import session_persistence as SessionPersistence

# ✓ 对
from domains.session.infrastructure.session_persistence import SessionPersistence
```

### 复现测试报 `RuntimeContext` 未安装

先调 `build_runtime_context()`（或 `bootstrap_context()`）。

### 会话列表里出现 `__value__` 开头的 id

历史脏数据（pydantic repr 泄漏到文件名）。`recover_session_id()` 会还原，
无需手动清理。

### 遥测无输出

当前 opentelemetry SDK 版本缺 `LoggingProvider`，遥测降级为仅本地 runlog
（`~/.rabbit/logs/telemetry-runlog.jsonl`）。这是可接受的降级，非故障。
