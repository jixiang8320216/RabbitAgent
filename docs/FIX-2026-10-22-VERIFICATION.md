# RabbitAgent 验证修复记录（2026-10-22）

本文件记录验证过程中发现并修复的缺陷。

> 说明：本文件在工作过程中曾被外部进程清空，历史章节（第一～十二节，涵盖
> L0 接入层收尾、Agent 架构定型 `preset_type`、删除 5 个冗余旧 agent 类等）
> 内容已随本次清空丢失。这些改动本身仍在代码库中，其决策与缺陷清单分别记录于：
>
> - `docs/AGENT-PRESET-ARCHITECTURE.md` —— Agent 架构定型（ReActAgent 单类 +
>   `preset_type` 配置预设）的决策、路由表与缺陷清单
> - `docs/TUI-INTERACTIVE-VERIFICATION.md` —— TUI 交互式问答端到端验证的
>   逐项修改前后对照
>
> 以下为当前保留的第十三节。

---

## 十三、TUI 交互式问答端到端验证（ConPTY 真实终端）

> 完整过程与逐项修改前后对照见 `docs/TUI-INTERACTIVE-VERIFICATION.md`，本节为摘要。
> 验证方式：`pywinpty` 给 `main.py` 分配真实终端（44×120），驱动脚本真发按键。

### 13.1 结论

启动 → `/help` → 自然语言提问拿到真实 LLM 答复 → `/exit` 干净退出，**全流程跑通**。

```
STEP1 banner_rendered=True
STEP2 help_dispatched=True
STEP3 question_echoed=True answer_rendered=True llm_http_200=True agent_turn_end=True
STEP4 exit_sent=True
```

三重证据：TUI 屏幕字节流（回显 `开门见山`）、应用日志
（`chat/completions HTTP/1.1 200 OK` + `Agent end successfully`）、
会话历史 `~/.rabbit/apps/*.jsonl`（`[user] 开门见山` → `[agent] 开门见山`）。

### 13.2 最关键缺陷：主循环 `else` 缩进错位

**现象**：提问在 TUI 上正常回显、`_tui_trace` 显示 `action_submit` 已把完整文本
交给 future，但会话历史查无此条、LLM 零调用。

**根因**：`interfaces/cli/tui_application.py` 主循环里 `else` 与**内层**
`if expansion is not None` 配对，而非外层 `if self._is_command_input(...)`。
命令分支自带 else，非命令（自然语言）输入落不到任何分支 —— **被静默丢弃**。
这也解释了「`/help` 能触发 LLM、自然语言不能」。

```diff
-                       else:
-                           # 处理普通用户消息
-                           await self._enqueue_user_message(user_input)
+                   else:
+                       # 处理普通用户消息
+                       await self._enqueue_user_message(user_input)
```

### 13.3 其余缺陷清单

**启动期崩溃（进程起来就退出）**

| # | 文件 | 缺陷 |
|---|---|---|
| 1 | `system_prompt_builder.py` | `__init__` 形参 `skills_md`，而整条链传 `rabbit_skills_md` → TypeError |
| 2 | `model_registry.py` | `RegisteredMode` 包装被当 `ModeDeclaration` 用（缺 `agent_slots`）→ 补 `__getattr__` 转发 |
| 3 | `model_registry.py` | `available_mode_descriptors()` 不存在（错误分支里的第二颗雷） |
| 4 | `runtime_composition.py` | `@dataclasses.dataclass(frozen=True)` 装饰 pydantic BaseModel，覆盖 `__init__` 使有默认值的字段也变必填 → 移除装饰器 |
| 5 | `runtime_composition.py` | `system_prompt_file` 既是字段又是同名 property（自我递归）；兼容别名应为 `sytem_prompt_file` |

**可观测性**

| # | 文件 | 缺陷 |
|---|---|---|
| 6 | `shared/paths.py` | `get_config_dir()` 用 `parents[2]` 多退一级 → `conf/logging.ini` 找不到 → **无文件日志 handler，所有 `logger.exception` 的真实错误全部丢失** |
| 7 | `conf/logging.ini` | 新建（自包含，只用标准库 handler） |
| 8 | `local_mcp_resources.py` | 同类 `parents[2]`（应为 `[3]`）→ 系统级 MCP 配置永不加载 |

**运行时（agent 回合崩溃）**

| # | 文件 | 缺陷 |
|---|---|---|
| 9 | `system_prompt_builder.py` | 缺 `_instruction_content` / `_project_instruction_content`（只有调用无定义） |
| 10 | 同上 | `hook_inputs` 只在 `TYPE_CHECKING` 下导入，运行期用 `InstructionMemoryType` → NameError |
| 11 | `react_agent_reply.py` | `response.tool_use_blocks` 不存在 → 改用 `get_content_blocks("tool_use")` |
| 12 | `react_agent_reply.py` | `self._host._complete_without_tools(response)` —— host 上无此方法，且真实签名是 3 参数 |
| 13 | `session_hook_lifecycle.py` | `self.app` → `self._app`（属性名漂移，4 处） |
| 14 | `session_resume_service.py` | 缺 `close()`（只有调用）+ 缺 `import inspect`；首版误用不存在的 `_loaded_registry` |
| 15 | `rabbit_application.py` | `shutdown_plugins(timeout=)` → `timeout_seconds=` |

**TUI 交互链路**

| # | 文件 | 缺陷 |
|---|---|---|
| 16 | `tui_application.py` | 主循环 `else` 缩进错位（见 13.2，最关键） |
| 17 | `tui_app.py` | `action_submit` 未收起补全列表 → 后续回车被补全消费，消息提交不出去 |
| 18 | `tui_app.py` | `_process_submission` 直接 `mount()` 绕过 `safe_mount` → `MountError` → 被记成异常退出 |

**架构守卫**

| # | 文件 | 缺陷 |
|---|---|---|
| 19 | `tools/check_architecture.py` | 新增测试依赖该脚本但文件不存在 → 按文档规格补齐 4 条规则 + CLI |
| 20 | `app_config.py` / `runtime_builder.py` | 反向依赖 `interfaces.acp.schema`（本体已在 `runtime_descriptors`）→ 改为直接引用本体，已验证仍是同一个类 |

### 13.4 回归

| 项目 | 结果 |
|---|---|
| `pytest test` | **306 passed / 15 skipped / 0 failed** |
| `tools/check_architecture.py` | **0 违规** |
| TUI 端到端 | STEP1–STEP4 全通过 |

> 非缺陷说明：中途出现的 `rag_agent_tools.py:41 IndentationError` 经核实是
> **外部进程正在写入该文件时的瞬时状态**，文件稳定后语法正常。
> `verify_tui_interactive.py` 验证完成后被外部清理，已重建到 `tools/` 下。
