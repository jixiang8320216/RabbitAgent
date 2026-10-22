# TUI 交互式问答端到端验证 —— 修改前后归档

> 验证时间：2026-10-22
> 被验证对象：`D:/AI/AIProject/RabbitAgent`（当前工作副本）
> 验证方式：Windows ConPTY 分配**真实终端**，驱动 `main.py` 真实发按键
> 结论：**启动 → `/help` → 自然语言提问拿到 LLM 答复 → `/exit` 干净退出，全流程跑通**

---

## 一、为什么要这样做

`main.py` 顶层 `except` 只 `print(f"\n Fatal error: {e}")`，**吞掉堆栈**；
而 Textual 在 stdout 不是终端时会退化、键盘输入无法注入。所以静态检查与
`pytest` 都覆盖不到「真实交互」这一段。

本次用 `pywinpty` 给 `main.py` 分配一个 ConPTY 真实终端（44×120），
再用驱动脚本真发按键，从而让「跑不起来」的缺陷无处可藏。

- 驱动脚本本身跑在装有 `pywinpty` 的托管环境；
- 被驱动的目标进程用 `RabbitAgent - 1018/.venv`（textual 8.2.8 / Python 3.14.5，
  依赖完整）；
- 但 `cwd` 与 `PYTHONPATH` 都指向**当前项目** —— 验证的是当前工作副本的代码。

---

## 二、最终验证结果

```
STEP1 banner_rendered=True
STEP2 help_dispatched=True
STEP3 question_echoed=True answer_rendered=True llm_http_200=True agent_turn_end=True
STEP4 exit_sent=True
```

三重证据交叉确认（不依赖单一信号）：

| 证据 | 来源 | 结果 |
|---|---|---|
| 提问完整回显 | TUI 屏幕字节流 | `请只回复四个字：开门见山。不要任何解释。` |
| 答复渲染到屏幕 | TUI 屏幕字节流 | `开门见山开门见山`（用户消息 + agent 答复） |
| 真实 LLM 调用 | 应用日志 | `POST https://api.deepseek.com/chat/completions "HTTP/1.1 200 OK"` |
| agent 回合成功 | 应用日志 | `Agent end successfully: 通用模式` |
| 落库 | 会话历史 `~/.rabbit/apps/*.jsonl` | `[user] 开门见山` → `[agent] 开门见山` |
| 干净退出 | 应用日志 | `exit requested by command` + `TUIApplication shutdown complete` |

> 判定特意选用高辨识度问答文本（"请只回复四个字：开门见山"）而非 `1+1`，
> 避免屏幕上大量噪声（命令列表、欢迎语）造成误判/漏判。

---

## 三、修改前后对照

### A 组：启动期崩溃（进程起来就退出）

#### A1. `SystemPromptBuilder.__init__()` 不接受 `rabbit_skills_md`

整条链（`system_resources → orchestrator → bootstrap → agent_factory`）都用
`rabbit_skills_md`，唯独 builder 形参写成 `skills_md` → `TypeError`，**进程启动即退出**。

```diff
# domains/agent/application/prompt/system_prompt_builder.py
     def __init__(
         self,
         *,
         config_manager: configuration.ConfigManager,
         context_optimizer: context_optimizer_module.ContextOptimizer|None = None,
         current_user_request: typing.Optional[str] = "",
         skill_registry: typing.Optional[skill_registry_module.SkillRegistry] = None,
-        skills_md: typing.Optional[str] = None,
+        # 与调用链保持一致的主参数名；``skills_md`` 保留为兼容别名
+        rabbit_skills_md: typing.Optional[str] = None,
+        skills_md: typing.Optional[str] = None,
         instruction_resources: ... = None,
     ) -> None:
         self.skill_registry = skill_registry
-        self.skills_md = skills_md
+        self.rabbit_skills_md = rabbit_skills_md
+        self.skills_md = rabbit_skills_md if rabbit_skills_md is not None else skills_md
```

#### A2. `RegisteredMode` 被当成 `ModeDeclaration` 用（契约漂移）

`ModelRegistry.get()` 实际返回 `RegisteredMode` **包装**，但全库消费点
（`agent_orchestrator` / `root_agent_loader` / `groupchat_agent_builder`）都按
`ModeDeclaration` 直接取 `agent_slots` / `default_contribution_policy` → `AttributeError`。

修法：在包装类上做属性转发，两种用法都成立（比改散落各处的消费点更安全）。

```diff
# domains/agent/application/registry/model_registry.py
     @property
     def name(self) -> str:
         return self.declaration.name
+
+    def __getattr__(self, item: str) -> typing.Any:
+        """把未知属性转发给内层 ``declaration``。
+
+        ``ModelRegistry.get()`` 返回的是本包装对象，但历史消费点（如
+        ``agent_orchestrator._interpret_mode``）按 ``ModeDeclaration`` 直接取
+        ``agent_slots`` / ``default_contribution_policy``。与其改散落各处的调用点，
+        不如让包装对象同时满足两种用法。
+        """
+        declaration = self.__dict__.get("declaration")
+        if declaration is not None and not item.startswith("_"):
+            return getattr(declaration, item)
+        raise AttributeError(item)
```

#### A3. `ModelRegistry.available_mode_descriptors()` 不存在

错误分支里的第二颗雷 —— 模式找不到时才走到，平时不触发，一触发就 `AttributeError`。

```diff
# domains/agent/application/registry/model_registry.py
+    def available_mode_descriptors(self) -> list[plugin_domain.ModeDeclaration]:
+        """返回当前已注册模式的声明对象列表。"""
+        with self._lock:
+            return [stack[-1].declaration for stack in self._mode_stacks.values() if stack]
```

调用点同步改为按声明对象取值（模式名是字符串，没有 `.value`）：

```diff
# domains/runtime/application/orchestrators/agent_orchestrator.py
-            available_modes=sorted(
-                descriptor.value for descriptor in self.app.mode_registry.available_mode_descriptors()
-            )
+            available_modes=sorted(
+                descriptor.name for descriptor in self.app.mode_registry.available_mode_descriptors()
+            )
```

顺带修正三处**过时且错误**的注释（"get() 直接返回 ModeDeclaration，没有
`.declaration` 包装"）—— 它们正是这次契约漂移的误导来源。

#### A4. `AgentRuntimeOptions`：`@dataclasses.dataclass` 覆盖了 pydantic 的 `__init__`

```diff
# domains/agent/application/lifecycle/runtime_composition.py
-@dataclasses.dataclass(frozen=True)
 class AgentRuntimeOptions(pydantic.BaseModel):
```

`@dataclasses.dataclass(frozen=True)` 装饰在 `pydantic.BaseModel` 上时，
dataclass 生成的 `__init__` 会**覆盖** pydantic 的构造器，把**有默认值的字段
也变成必填**。实测：

```python
@dataclasses.dataclass(frozen=True)
class T(pydantic.BaseModel):
    a: int = 1
    b: str = ""
    c: bool = False

T(a=2)  # TypeError: missing 2 required positional arguments: 'b', 'c'
```

而 `_options()` 只传 6 个参数 → `TypeError`。全库 AST 扫描确认这是**唯一**一处
`dataclass + pydantic.BaseModel` 组合。

#### A5. `system_prompt_file` 既是字段又是同名 property（自我递归）

```diff
# domains/agent/application/lifecycle/runtime_composition.py
-    @property
-    def system_prompt_file(self) -> str:
-        """历史拼写（缺一个 s）的兼容别名，指向 ``system_prompt_file``。"""
-        return self.system_prompt_file      # ← 无限递归
+    @property
+    def sytem_prompt_file(self) -> str:
+        """历史拼写（缺一个 s）的兼容别名，指向 ``system_prompt_file``。"""
+        return self.system_prompt_file
```

> 非缺陷说明：中途出现的 `rag_agent_tools.py:41 IndentationError` 经核实是
> **外部进程正在写入该文件时的瞬时状态**，文件稳定后语法正常，未做改动。

---

### B 组：可观测性缺失（错误全部丢失）

#### B1. `get_config_dir()` 多退了一级 —— 根因

```diff
# shared/paths.py
 def get_config_dir()->Path:
-    return Path(__file__).resolve().parents[2]/"conf"
+    return Path(__file__).resolve().parents[1]/"conf"
```

`shared/paths.py` 位于 `<项目根>/shared/paths.py`：`parents[0]=shared`、
`parents[1]=项目根`。原写法 `parents[2]` 解析成 `D:\AI\AIProject\conf`，
而当前项目根本没有 `conf/` → **`logging.ini` 找不到 → 无文件日志 handler →
所有 `logger.exception` 的真实错误全部丢失**。这是本轮排查最大的障碍。

#### B2. 新建 `conf/logging.ini`

自包含配置，只用标准库 handler（不依赖 `-1018` 独有的 `logutils` / `handlers` 模块）。

#### B3. `local_mcp_resources.py` 同类缺陷

```diff
# domains/resource_catalog/infrastructure/local_mcp_resources.py
-        system_dir = Path(__file__).resolve().parents[2] / "conf"
+        system_dir = Path(__file__).resolve().parents[3] / "conf"
```

该文件在 `domains/resource_catalog/infrastructure/` 下，`parents[2]` 只到
`domains/`，`parents[3]` 才是项目根。原写法导致**系统级 MCP server 配置永远不加载**。

---

### C 组：运行时缺陷（agent 回合崩溃）

#### C1. `SystemPromptBuilder` 缺两个方法（只有调用、没有定义）

`_instruction_content()` / `_project_instruction_content()` 全库只有调用点。

```diff
# domains/agent/application/prompt/system_prompt_builder.py
+    def _instruction_content(self, memory_types) -> str:
+        """按 ``memory_type`` 过滤指令资源并聚合成一段文本。"""
+        wanted = set(memory_types or ())
+        if not wanted:
+            return ""
+        parts: list[str] = []
+        for resource in self.instruction_resources or []:
+            if getattr(resource, "memory_type", None) not in wanted:
+                continue
+            text = _safe_str(getattr(resource, "content", ""))
+            if text:
+                parts.append(text)
+        return "\n\n".join(parts)
+
+    def _project_instruction_content(self, project_home: str | None) -> str:
+        """取 PROJECT 级指令内容；未指定工作目录时返回空串。"""
+        if not project_home:
+            return ""
+        ...
```

#### C2. `hook_inputs` 只在 `TYPE_CHECKING` 下导入，运行期 `NameError`

```diff
# domains/agent/application/prompt/system_prompt_builder.py
 if typing.TYPE_CHECKING:
     import domains.configuration.infrastructure.adapter.config_manager as configuration
     import domains.skill.service.skill_registry as skill_registry_module
     import domains.configuration.domain.runtime_state.system_resources as system_resource_module
-    import domains.hook.domain.inputs as hook_inputs
+# ``InstructionMemoryType`` 在 ``_instruction_content`` 里是运行期判据，
+# 不能只留在 TYPE_CHECKING 下，否则一进该方法就 NameError。
+import domains.hook.domain.inputs as hook_inputs
```

#### C3. `response.tool_use_blocks` 属性不存在

```diff
# domains/agent/domain/aggregates/react_agent_reply.py
-            tool_use_blocks = response.tool_use_blocks
+            # 本项目用 ``Msg.get_content_blocks("tool_use")``，没有 ``tool_use_blocks`` 属性
+            tool_use_blocks = response.get_content_blocks("tool_use")
```

#### C4. `_complete_without_tools` 调用错了对象且参数不全

`ReActAgent` 上**没有** `_complete_without_tools`；真正的方法在本模块，签名是
**3 参数** `(response, turn, iteration)`。

```diff
# domains/agent/domain/aggregates/react_agent_reply.py
-                return await self._host._complete_without_tools(response)   # 两处
+                return await self._complete_without_tools(response, turn, iteration)
```

#### C5. `session_hook_lifecycle.py` 属性名漂移（4 处）

`__init__` 存的是 `self._app`，方法里用 `self.app`。

```diff
-self.app.observer ...   →  self._app.observer ...     （4 处）
```

#### C6. `SessionResumeService.close()` 只有调用、没有定义

```diff
# domains/agent/application/lifecycle/session_resume_service.py
+import inspect
+
+    async def close(self) -> None:
+        """``RabbitApplication.dispose_hook_resources`` 会调用它收尾。"""
+        registry = self.load_registry()
+        if registry is not None:
+            closer = getattr(registry, "close", None)
+            if callable(closer):
+                result = closer()
+                if inspect.isawaitable(result):
+                    await result
+        self._registry_loader = None
+        self._app_getter = None
```

> 首版实现引用了不存在的 `self._loaded_registry`，已按 `__init__` 实际属性名
> （`_registry_loader` / `_app_getter`）改正，并补上缺失的 `import inspect`。

#### C7. `shutdown_plugins` 关键字参数名错

```diff
# domains/runtime/application/rabbit_application.py
-            await self.plugin_manager.shutdown_plugins(timeout=timeout_seconds)
+            await self.plugin_manager.shutdown_plugins(timeout_seconds=timeout_seconds)
```

签名是 `async def shutdown_plugins(self, timeout_seconds: float = 60.0)`。
修完退出路径的 `dispose_hook_resources failed` 告警消失。

---

### D 组：TUI 交互链路（本次最关键）

#### D1. 主循环 `else` 缩进错位 —— **自然语言提问被静默丢弃**

这是本轮最严重、也最隐蔽的缺陷。现象：提问在 TUI 上正常回显、
`_tui_trace` 也显示 `action_submit` 已把完整文本交给 future，
但**会话历史里查无此条、LLM 零调用**。

根因：`else` 与**内层** `if expansion is not None` 配对，而不是外层的
`if self._is_command_input(...)`。于是命令分支自带 else，
非命令（自然语言）输入落不到**任何**分支，被静默丢弃。

```diff
# interfaces/cli/tui_application.py
                    if self._is_command_input(user_input):
                        expansion = await self._handle_command(user_input)
                        if expansion is not None:
                            await self._enqueue_user_message(
                                expansion.expanded_prompt,
                                prompt_expansion=expansion,
                            )
-                       else:
-                           # 处理普通用户消息
-                           await self._enqueue_user_message(user_input)
+                   else:
+                       # 处理普通用户消息
+                       await self._enqueue_user_message(user_input)
```

> 这也解释了「`/help` 能触发 LLM、自然语言不能」：命令走 `_handle_command`
> 内部自有通道，不受这个 else 影响。

#### D2. `action_submit` 提交时未收起补全列表

提交后补全列表继续保有 `show` 类，后续按键都被 `_route_key` 判定为
「补全列表可见」而转给 `_handle_completion_list_key` —— 下一次回车被回填
逻辑消费，消息提交不出去。

```diff
# interfaces/tui/widgets/tui_app.py
     def action_submit(self)->None:
         if self.awaiting_interrupt_confirm:
             return
+        # 提交前必须收起补全列表：否则它继续保持 ``show`` 类，后续按键都会被
+        # 转给补全处理分支，下一次回车被回填逻辑消费，消息永远提交不出去。
+        self.hide_list()
         text_area = self.query_one("#user-input", UserInputArea)
```

#### D3. `_process_submission` 绕过 `safe_mount` 直接 `mount()`

```diff
# interfaces/tui/widgets/tui_app.py
-            chat_area = self.query_one("#chat-area")
-            chat_area.mount(Static(f"{user_input}",classes="user-msg"))
+            try:
+                chat_area = self.query_one("#chat-area", VerticalScroll)
+            except Exception:
+                chat_area = None
+            if chat_area is not None:
+                self.safe_mount(chat_area, Static(f"{user_input}", classes="user-msg"))
+            else:
+                logger.debug("user echo skipped: #chat-area unavailable")
```

原写法在「`#chat-area` 自身尚未 `is_mounted`」的窗口里（TUI 刚启动或 `/exit`
正在拆卸界面）抛 `MountError: Can't mount widget(s) before VerticalScroll(...) is
mounted`，被 Textual 判成 unhandled exception → `App.return_code=1`，
一次本该成功的问答被记成异常退出。

---

### E 组：架构守卫（新增测试暴露的缺口）

#### E1. 新建 `tools/check_architecture.py`

`test/test_architecture_guard.py` 与 `test/test_runtime_landmines.py`（外部新增，
未跟踪）依赖该脚本，但它并不存在 → 3 条用例失败。按文档规格补齐：

| 规则 | 含义 |
|---|---|
| `R1-domain-purity` | 领域层不依赖本域 `infrastructure` |
| `R2-cross-domain-infrastructure` | 领域层不跨域直接依赖对方 `infrastructure` |
| `R3-domain-no-interfaces` | `domains/**` 不依赖 `interfaces/**` |
| `R4-lazy-export-shadows-submodule` | 惰性导出名不得与本包子模块同名 |

同时导出 `_collect_lazy_map()` / `_submodule_names()` 供
`test_no_lazy_export_shadows_a_submodule` 复用，避免两份判定逻辑漂移。
CLI 支持 `--json` / `--baseline` / `--update-baseline`。

> 规则边界说明：R1/R2 只约束**领域层**。应用层编排时引用对方的 adapter/store
> 是组合根的正常职责，一刀切会把正常装配代码全判成违规（实测 160 条 → 0 条）。

#### E2. 两处真实的反向依赖（域层 → 展示层）

`ModelInfo` / `SessionMode` 的定义本体**早已下沉**到配置域的
`runtime_descriptors`，`interfaces/acp/schema.py` 只是一层 re-export。
但仍有两处直接引用 re-export，把依赖方向拧反了：

```diff
# domains/configuration/domain/model/app_config.py
 if TYPE_CHECKING:
-    from interfaces.acp.schema import ModelInfo, SessionMode  # noqa: F401
+    from domains.configuration.domain.model.runtime_descriptors import (  # noqa: F401
+        ModelInfo,
+        SessionMode,
+    )

# domains/context/runtime_builder.py
-from interfaces.acp.schema import ModelInfo, SessionMode
+from domains.configuration.domain.model.runtime_descriptors import (
+    ModelInfo,
+    SessionMode,
+)
```

已验证拿到的仍是**同一个类**（`A is C` / `B is D` 均为 `True`），行为不变。

---

## 四、回归验证

| 项目 | 结果 |
|---|---|
| `pytest test` | **306 passed / 15 skipped / 0 failed** |
| `tools/check_architecture.py` | **0 违规** |
| TUI 端到端 | STEP1–STEP4 全通过（见第二节） |

15 条 skip 均为环境依赖（真实 LLM 质量指标、`qdrant-client` 未装、
Windows 符号链接），与基线一致，不属于缺陷。

---

## 五、复现

```bash
# 1) 架构守卫
python tools/check_architecture.py            # 0 违规
python tools/check_architecture.py --json     # CI 消费

# 2) 全量回归
python -m pytest test -q --no-header -p no:cacheprovider --tb=line --no-baseline --no-report

# 3) TUI 真实交互（ConPTY；需 pywinpty + 完整依赖 venv）
python verify_tui_interactive.py
# 产物：_tui_e2e_clean.log（干净屏幕流）、_tui_e2e_report.txt（机器判定）
```

TUI 驱动脚本要点（避免重踩）：

1. **必须逐字符发送**。ConPTY 一次性写入整串会丢字符（实测
   `1+1 等于几？` 被截成 `1+`）。
2. **不要用 `/clear`** 做清屏。它自身会渲染一个带时间戳的
   `Command Result` 块，会被误判成 agent 答复锚点。用 `Esc` 替代。
3. **判定要交叉验证**。TUI 全屏重绘 + 去重会让屏幕流漏判，需同时读应用日志
   （`~/.rabbit/logs/rabbit.log`）与会话历史（`~/.rabbit/apps/*.jsonl`）。
4. **输入阶段与提交阶段分段收集**。逐字符回显的中间态会污染答复判定。
5. **日志基线用字符数**而非 `st_size`（日志含中文，字节与字符索引不一致）。

---

## 六、遗留

- `verify_tui_interactive.py` 在验证完成后被外部进程清理，建议重建到
  `tools/` 下作为长期回归资产（要点见上一节）。
- `rag_agent_tools.py` 的瞬时 `IndentationError` 提示有外部进程在并发改写
  项目文件，后续验证前建议先确认工作副本稳定。
