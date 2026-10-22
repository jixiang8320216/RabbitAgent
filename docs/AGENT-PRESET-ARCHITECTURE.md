# Agent 模块架构定型：ReActAgent 单类 + `preset_type` 配置预设

> 日期：2026-10-22
> 范围：`domains/agent/domain/aggregates/` 下的 6 个 agent 类，
> 以及从 `runtime_composition._instantiate_agent` 到 `ReActAgent._reply_impl` 的完整装配链路。

---

## 一、待决的问题

`domains/agent/domain/aggregates/` 下有 6 个 agent 类：

| 类 | 文件 |
|---|---|
| `ReActAgent`（另有别名 `ReactAgent`） | `react_agent.py` |
| `SimpleAgent` | `simple_agent.py` |
| `FunctionCallAgent` | `function_call_agent.py` |
| `PlanAndSolveAgent` | `plan_solve_agent.py` |
| `ReflectionAgent` | `reflection_agent.py` |
| `ToolAwareSimpleAgent` | `tool_aware_agent.py` |

需要确认三件事：

1. **架构选型**：`ReActAgent` 作基类、其余 5 个作「配置类」？还是全部集成进 `ReActAgent`？
2. **实现替换**：是否用 `_reply_*` 的内部实现替换 `async def reply` 的内部实现？
3. **改造起点**：从 `domains.agent.application.lifecycle.runtime_composition._instantiate_agent` 开始。

---

## 二、两个方案的对比

### 方案 A：继承 —— `ReActAgent` 作基类，5 个子类各自重写 `reply`

```
Agent(ABC) / ReActAgent
    ├── SimpleAgent
    ├── FunctionCallAgent
    ├── PlanAndSolveAgent
    ├── ReflectionAgent
    └── ToolAwareSimpleAgent
```

### 方案 B：集成 —— 单一 `ReActAgent`，由配置参数 `preset_type` 选择内部回复策略

```
AgentRuntime.preset_type ∈ {react, simple, plan_solve, reflection, function_call, tool_aware}
        ↓ 透传
ReActAgent(preset_type=...)
        ↓ _resolve_reply_strategy()
_reply_reflection / _reply_plan_solve / _reply_text_parse / ReAct 主循环
```

### 判据

| # | 判据 | 结论 |
|---|---|---|
| 1 | `agent_runtime.py` 中 `preset_type` 的**源码注释原文**：「预设类型（**替代6个独立类**）」 | 方案 B |
| 2 | `AgentPresetType = Literal["react","simple","plan_solve","reflection","function_call","tool_aware"]` —— 正好这 6 个类 | 方案 B |
| 3 | `tool_call_mode` 的注释原文：「**原SimpleAgent**="text_parse"、**FunctionCallAgent**="native"」 | 方案 B |
| 4 | `_instantiate_agent` 从头到尾只构造 `ReActAgent`（`react_agent_factory` / `create_agent_instance`） | 方案 B |
| 5 | 5 个兄弟类属旧 `Agent(ABC)` 框架：构造签名是 `(name, llm, system_promot, config)`，与 `ReActAgent` 的 `(name, sys_prompt, model, formatter, toolkit, context, ...)` 完全不兼容 | 否 A |
| 6 | 5 个兄弟类的抽象 `reply` **未实现**（`Agent.reply` 是 `@abstractmethod`），且运行时无任何引用点 | 否 A |
| 7 | `ReActAgent` 已承载 toolkit / plan_runtime / hooks / context / observer / 中断 / 持久化 / 执行存储；若走继承，每个子类都要重接一遍，必然分叉 | 方案 B |
| 8 | `_reply_impl` 已是既定的分流点，`reply` 只剩 hook 外壳 —— 正是「`_reply_*` 内部实现替换 `reply` 内部实现」的既定结构 | 方案 B |

### 决策

> **采用方案 B：全部集成进 `ReActAgent`，由 `preset_type` 配置参数选择行为。**
> 这不是本次新提出的重构，而是**代码自身已经写明、但链路未接通的既定设计**——
> `preset_type` 字段已在 `AgentRuntime` 上定义，却从未被任何代码读取
> （改造前全库 `grep preset_type` 只有定义处 1 处命中）。本次工作是把它接通。

**关于第 2 问（`_reply_*` 替换 `reply` 内部实现）：是，且已按此定型。**
`reply` 只保留 `pre_reply` / `model_error` / `post_reply` 的生命周期外壳；
全部策略主体下沉到 `_reply_impl` 及其分支方法。新增一种 agent 行为只需
加一个 `_reply_xxx` 方法 + 在 `_PRESET_REPLY_STRATEGY` 登记，无需新建类。

---

## 三、路由表

`ReActAgent._PRESET_REPLY_STRATEGY`（`react_agent.py`）：

| `preset_type` | 策略 | 对应原类 | 说明 |
|---|---|---|---|
| `react` | `react` | `ReActAgent` | 默认，原生 ReAct 主循环 |
| `function_call` | `react` | `FunctionCallAgent` | native 工具调用 = ReAct 主循环 |
| `tool_aware` | `react` | `ToolAwareSimpleAgent` | ReAct + `tool_call_listener` |
| `simple` | `text_parse` | `SimpleAgent` | 无 native 工具调用，走文本标记解析 |
| `plan_solve` | `plan_solve` | `PlanAndSolveAgent` | 规划 → 逐步执行 |
| `reflection` | `reflection` | `ReflectionAgent` | 初始作答 → 反思 → 优化循环 |

`react` / `function_call` / `tool_aware` 属同一家族（`_REACT_FAMILY_PRESETS`），
在此基础上 `tool_call_mode="text_parse"` 可单独把工具调用切成文本解析模式。

**优先级**：显式 `preset_type` → `tool_call_mode` → 旧启发式（向后兼容）。
未知的 `preset_type` 记 warning 并安全降级为 `react`。

### 完整链路

```
AgentRuntime.preset_type            (domains/agent/domain/entities/agent_runtime.py)
    ↓
_instantiate_agent()                (runtime_composition.py)  ← 改造起点
    ↓  preset_type= / tool_call_mode= / reflection_* / plan_prompts
AgentInstanceManager.create_agent_instance()   (instance_manager.py)
    ↓  或 react_agent_factory(...)
ReActAgent.__init__(preset_type=...)           (react_agent.py)
    ↓
ReActAgent.reply()          ← 只剩 hook 生命周期外壳
    ↓
ReActAgent._reply_impl()    ← 策略路由主体
    ↓  _resolve_reply_strategy()
_reply_reflection / _reply_plan_solve / _reply_text_parse / run_react_reply
```

---

## 四、本次修复的缺陷

### 4.1 链路断裂（阻断级）

| # | 位置 | 缺陷 | 后果 |
|---|---|---|---|
| 1 | `runtime_composition._instantiate_agent` | 传 `tool_callmode=`，而 `ReActAgent.__init__` 的参数是 `tool_call_mode` | 关键字不匹配 → `TypeError`，该分支**根本无法执行** |
| 2 | 同上 | 从未读取 `sub_agent.preset_type` | `preset_type` 成为死字段，配置选择完全失效 |
| 3 | `instance_manager.create_agent_instance` | 签名不含 `tool_call_mode` / `reflection_*` / `plan_prompts` / `preset_type`，但 `_instantiate_agent` 会传入 | `TypeError: unexpected keyword argument` |

### 4.2 `ReActAgent.__init__`

| # | 缺陷 | 后果 |
|---|---|---|
| 4 | `self.plan_runtime = plan_prompts or _DEFAULT_PLANNER_PROMPTS` 覆盖了第 191 行注入的真实 `PlanRuntime` | `plan_runtime` 变成 prompts 字典，`_reply_impl` 里 `plan_runtime is None` 永假 → plan_solve 永不触发 |
| 5 | `self.plan_prompts` 从未被赋值 | 一旦走到 `_reply_plan_solve` 即 `AttributeError` |
| 6 | 无 `preset_type` 参数 | 配置无法传入 |

### 4.3 三条策略实现（此前全是死代码 + 必然崩溃）

| # | 缺陷 | 出现位置 |
|---|---|---|
| 7 | `self.content.add(...)` —— 属性不存在（应为 `self.context`） | `_reply_text_parse` / `_reply_plan_solve` / `_reply_reflection` |
| 8 | `self.formatter.format(self._sys_prompt, self.context)` —— formatter 实为 `async def format(*, msgs)`，未 `await` 只拿到协程 | 同上 |
| 9 | `await self.model(prompt_str)` —— 模型要的是**已格式化的消息列表**，不是字符串 | 同上 |
| 10 | `message.Msg(role=..., content=...)` —— `Msg` 签名是 `(name, content, role)`，缺 `name` 且顺序错 | 共 11 处 |
| 11 | `prompts["initial"].format(question=task)` —— 模板用的是 `{task}` | `KeyError: 'task'`，反思第一步即崩 |
| 12 | `_reply_text_parse` 循环体在首轮就 `return`，注释写「继续循环」实际不循环 | 逻辑与注释不符 |
| 13 | `post_reasoning` 载荷传原始 response，而主循环约定必须是 `Msg`（`_apply_post_reasoning_hook` 会校验） | hook 类型不一致 |
| 14 | `_reply_text_parse` 无任何入口调用（`tool_call_mode` 未接线） | 死代码 |

### 4.4 修复方式

- 新增 `_generate_turn()`：统一「格式化上下文 → 调模型 → `collect_model_turn`」，
  与 ReAct 主循环 `react_agent_reply._collect_turn` 完全同构（8/9 一并解决）。
- 新增 `_format_prompt()`：同时注入 `{task}` / `{question}` 两个别名，
  使内置模板与用户自定义模板都能渲染（11）。
- 新增 `_notify_text_stream_end()`：供三条策略复用流式收集器。
- 全部 `Msg` 改为 `(self.name, content, role)`（10）。
- 重写 `_reply_impl` 为 `_resolve_reply_strategy` + 显式路由（2/6/14）。
- `_reply_text_parse` 的循环改为真正迭代，耗尽后兜底总结（12）。

---

## 五、验证

### `verify_agent_preset_routing.py`（无网络、无真实 LLM）

```
合计：OK=33  FAIL=0  INFO=1
```

覆盖：
- A. 6 个预设全部登记策略、逐个路由正确、`tool_call_mode` 覆盖、未知值安全降级、旧启发式仍生效
- B. `__init__` 不再覆盖 `plan_runtime`、正确设置 `plan_prompts`、接收 `preset_type`
- C. `_instantiate_agent` 透传 `preset_type` 且使用正确拼写 `tool_call_mode`；`create_agent_instance` 接受全部新参数
- D. `reply` 仅保留 hook 外壳、`_reply_impl` 路由到三条策略
- E. 三条策略**真实跑通**（`_reply_plan_solve` 调模型 3 次产出「步骤1结果步骤2结果」；`_reply_reflection` 调 2 次收敛于「初始答案」）

> B、C 两项用 **AST** 判定而非文本匹配——源码注释里会提到历史写法，
> 文本匹配会把注释误判为缺陷。

### 回归

| 项目 | 结果 |
|---|---|
| `pytest test` | **227 passed / 14 skipped / 0 failed** |
| 全库导入自检 | **716 模块，0 导入期代码缺陷** |
| `verify_agent_module.py` | 32 OK / 0 FAIL |

14 个 skip 均为验证指南 L6 规定的「需真实 LLM / 外部服务 / 平台能力」场景，与基线一致。

---

## 六、如何新增一种 agent 行为

1. 在 `react_agent.py` 增加 `async def _reply_xxx(self, msg) -> message.Msg`；
2. 在 `_PRESET_REPLY_STRATEGY` 登记 `"新预设": "xxx"`；
3. 在 `_reply_impl` 加一行 `if strategy == "xxx": return await self._reply_xxx(msg)`；
4. 在 `AgentPresetType`（`agent_runtime.py`）加入新字面量。

**不需要新建类、不需要改装配链路。**

---

## 七、已删除的旧独立类

`preset_type` 预设接通后，以下 5 个文件已**删除**（`git rm`，可从版本历史恢复）：

| 文件 | 原类 | 承接它的预设 |
|---|---|---|
| `simple_agent.py` | `SimpleAgent` | `preset_type="simple"` → `_reply_text_parse` |
| `function_call_agent.py` | `FunctionCallAgent` | `preset_type="function_call"` → ReAct 主循环 |
| `plan_solve_agent.py` | `PlanAndSolveAgent` | `preset_type="plan_solve"` → `_reply_plan_solve` |
| `reflection_agent.py` | `ReflectionAgent` | `preset_type="reflection"` → `_reply_reflection` |
| `tool_aware_agent.py` | `ToolAwareSimpleAgent` | `preset_type="tool_aware"` → ReAct + `tool_call_listener` |

### 删除前必须先迁移的 1 个真实依赖

`reflection_agent.py` 里的 `Memory`（反思轨迹记忆）**不是** agent 类，
且有两个真实消费者，不能随文件一起删：

- `domains/memory/types/working.py` —— 对外 re-export，
  `domains.memory.types.Memory` 的惰性导出目标指向它；
- `test/stage3_cost/test_l2_orchestration.py::test_reflection_memory`（黄金用例）。

处理：新建 `domains/memory/types/reflection_memory.py` 承接该类，
更新 `working.py` 的导入与上述测试的导入路径，
并把 4 处惰性导出表中的 `Memory` 重指向新模块。

### 同步清理

- 惰性导出表（`aggregates/__init__.py`、`domains/agent/domain/__init__.py`、
  `domains/agent/__init__.py`、根 `init.py`）移除 5 个类名条目，
  以及随这些模块一起消失且全库无引用的附属符号
  （`Planner` / `Executor` / `DEFAULT_PLANNER_PROMPT` /
  `DEFAULT_EXECUTOR_PROMPT` / `DEFAULT_PROMPTS`）；`init.py` 的显式
  `__all__` 同步删名。
- `verify_agent_module.py` 的「兄弟类构造」检查改为「preset_type 预设覆盖」检查。

### 删除后验证

| 项目 | 结果 |
|---|---|
| 全库导入自检 | **712 模块 / 0 导入期代码缺陷** |
| `pytest test` | **227 passed / 14 skipped / 0 failed**（与删除前一致） |
| `verify_agent_module.py` | 23 OK / 0 FAIL |
| `verify_agent_preset_routing.py` | 33 OK / 0 FAIL |
| `domains.memory.types.Memory` / `domains.agent.domain.aggregates.Memory` | 均可解析 |
| `domains.agent.domain.aggregates.SimpleAgent` | 已正确移除 |
