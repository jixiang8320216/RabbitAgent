# 开发指南

面向要修改 RabbitAgent 代码的贡献者。

---

## 一、环境搭建

### 自动脚本

```bash
# Windows
rabbit-setup.bat

# macOS / Linux
./rabbit-setup.sh
```

### 手动

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 依赖分类

`requirements.txt` 用注释明确区分了两类依赖：

**硬依赖**（缺任一项则 import 即失败）：

| 包 | 用途 |
|---|---|
| `shortuuid` | `domains/agent/domain/value_objects/message.py`、`domains/plan/domain/execution_plan.py` |
| `dashscope` | `domains/plugin/domain/contribution.py`（ToolDefinition） |
| `fsspec` | `domains/persistence/infrastructure/file/_types.py`（原子写） |
| `transformers` | `domains/llm/provider/application/message_formatters.py`（MultiModalData） |
| `datasets` | `domains/rl/datasets.py` |
| `fastmcp` | `domains/protocol/mcp/client.py`、`server.py` |
| `huggingface-hub` | `domains/evaluation/benchmarks/data_generation/dataset.py` |

**可选依赖**（代码内已做 try/except 降级）：`ddgs` `markdownify` `tavily-python`
`serpapi` `qdrant-client` `neo4j` `Pillow` `scikit-learn` `markitdown`
`langdetect` `a2a-sdk` `Flask`

---

## 二、验证流程

改完代码**按顺序**跑这三层：

### 2.1 语法检查

```bash
python -c "
import ast, pathlib
n = 0
for py in pathlib.Path('.').rglob('*.py'):
    if any(x in {'.venv','__pycache__','.idea','.rabbit'} for x in py.parts): continue
    try: ast.parse(py.read_text(encoding='utf-8-sig'))
    except SyntaxError as e: n += 1; print(' ', py, e.lineno)
print(f'语法错误 {n} 个')"
```

### 2.2 全模块导入

遍历所有 `.py` 逐个 import。这是最容易发现问题的一层 —— 拼写错误、循环导入、
坏映射都在这里暴露。

### 2.3 端到端

```bash
python e2e_tui_test.py
```

11 项检查：app_mode 解析 / build_runtime / TUIApplication 装配 / 绑定请求上下文 /
加载 agent 配置 / 创建 agent / 初始化会话 / 状态栏同步 / 首屏渲染 / 命令注册 /
收到模型响应。

> 需要真实 LLM 配置（`~/.rabbit/config.json`）与网络。
> 加 `--no-llm` 参数可只验 UI 层（6 项）。

### 2.4 惰性导出验证

改了任何 `__init__.py` 的 `_LAZY` 映射后，**必须**跑：

```bash
python -c "
import importlib, pathlib
bad = []
for init in pathlib.Path('.').rglob('__init__.py'):
    if any(x in {'.venv','__pycache__','.idea','.rabbit'} for x in init.parts): continue
    pkg = '.'.join(init.parent.parts)
    try: mod = importlib.import_module(pkg)
    except Exception: continue
    for name in getattr(mod, '__all__', []):
        try: getattr(mod, name)
        except Exception as e: bad.append((pkg, name, type(e).__name__, str(e)[:60]))
for b in bad: print(' ', b)
print(f'坏导出 {len(bad)} 个')"
```

> ⚠️ **这一步不能省。** 惰性映射的失效在**导入期不报错**，只在**访问属性时**才炸。
> 合并包、改名、挪文件之后尤其容易漏掉「透传型」映射。

---

## 三、编码约定

### 3.1 惰性导出

所有 `domains/*/__init__.py` 与 `interfaces/*/__init__.py` 用 PEP 562：

```python
_LAZY = {
    "新符号": "所在模块路径",
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

**新增公开符号必须登记**。理由：显式映射让漏改在导入时立刻暴露；
扫描式回退在模块被移动后静默失效。

### 3.2 文档字符串

每个模块开头写清**为什么这样设计**，不只是**做了什么**。尤其是空壳模块 ——
空文件读者无法判断是「待实现」还是「忘了删」，因此即使保留空壳也要写明用途与现状。

### 3.3 历史缺口标注

修复历史缺陷时，在代码里留一行说明，便于后来者理解为何会有这段：

```python
# 历史缺口：此前提交_async 正常路径返回内部包装字典，
# 调用方拿到的不是真正的返回值。
```

### 3.4 导入规范

- **绝对导入**，全路径带前缀（`domains.xxx` / `interfaces.xxx` / `shared.xxx`）
- 跨域只依赖对方的 `domain` 或 `application`
- 可选依赖的 try 块**只 import 实际用到的符号**

---

## 四、常见坑

### 4.1 可选依赖的 try 块

```python
# ✗ 错：一个用不到的符号失效，让整个模块降级
try:
    from qdrant_client.http.models import (
        Distance, VectorParams, SearchRequest   # SearchRequest 在 1.19 已移除
    )
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

# ✓ 对：只 import 实际用到的，并把真实原因带进日志
try:
    from qdrant_client.http.models import Distance, VectorParams
    QDRANT_AVAILABLE = True
except ImportError as _exc:
    QDRANT_AVAILABLE = False
    _QDRANT_IMPORT_ERROR = _exc
```

**真实后果**：上例曾让 Qdrant 后端在库已安装的情况下误报「未安装」，完全不可用。

### 4.2 模块别名当类名用

```python
# ✗ 错：把模块别名成了类名
from domains.session.infrastructure import session_persistence as SessionPersistence
SessionPersistence.list_sessions(...)   # AttributeError

# ✓ 对
from domains.session.infrastructure.session_persistence import SessionPersistence
```

### 4.3 pydantic 内部结构泄漏

`model_dump()` 作用在**空模型**（`class AppState(BaseModel): ...`）上会产出
pydantic 内部序列化产物（`__value__` 包裹）。要读原始 JSON 就直接 `json.loads`。

### 4.4 字段名不匹配

`RuntimeContext` 的字段是 `apps_dir`（不是 `app_dir`）、`sessions_dir`。
写代码前先确认字段名，别凭直觉。

### 4.5 循环导入

惰性导出（PEP 562）能解决大部分。但若 domain 层需要反向引用 infrastructure
的某个符号，用**函数内延迟导入**：

```python
def normalize_llm_config(config):
    # 延迟导入：provider 侧会反向依赖本模块的 LLMConfig，顶层导入会形成循环。
    from domains.llm.provider import composition as provider_composition
    ...
```

### 4.6 改名/移动后必须重跑导出验证

见 [2.4](#24-惰性导出验证)。改名会让「透传型」映射静默失效。

---

## 五、代码审计脚本

工程里有几个高频审计动作，值得复用：

### 5.1 扫描坏映射

指向不存在模块的 `_LAZY` 映射：

```python
import ast, pathlib
ROOT = pathlib.Path('.')
real = set()
for p in ROOT.rglob('*.py'):
    if any(x in {'.venv','__pycache__','.idea'} for x in p.parts): continue
    rel = p.relative_to(ROOT).with_suffix('')
    parts = list(rel.parts)
    if parts[-1] == '__init__': parts = parts[:-1]
    if parts: real.add('.'.join(parts))

bad = []
for p in ROOT.rglob('__init__.py'):
    if any(x in {'.venv','__pycache__','.idea'} for x in p.parts): continue
    try: tree = ast.parse(p.read_text(encoding='utf-8-sig'))
    except Exception: continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                    if isinstance(v.value, str) and v.value.count('.') >= 2 \
                            and v.value not in real:
                        bad.append((str(p), k.value, v.value))
for b in bad: print(' ', b)
print(f'坏映射 {len(bad)} 条')
```

### 5.2 扫描「用了 X.Y 但 X 未导入」

比逐个追 `AttributeError` 快得多：

```python
import ast, pathlib
for py in pathlib.Path('domains').rglob('*.py'):
    if '__pycache__' in py.parts: continue
    try: tree = ast.parse(py.read_text(encoding='utf-8-sig'))
    except Exception: continue
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names: imported.add(a.asname or a.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names: imported.add(a.asname or a.name)
    used = {n.value.id for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    missing = used - imported - set(dir(__builtins__))
    if missing: print(f'  {py}: {sorted(missing)}')
```

### 5.3 找 0 字节空壳

```bash
find domains interfaces shared -name "*.py" -size 0 -not -path "*__pycache__*"
```

空壳模块要么补齐（按**调用契约**，不是凭名字猜），要么写明用途后保留，要么删除。

### 5.5 架构约束检查（三条硬规则的可执行版本）

```bash
python tools/check_architecture.py              # 违规 -> 退出码 1
python tools/check_architecture.py --json       # CI 消费
python tools/check_architecture.py --baseline   # 只拦新增违规（存量先进基线）
python tools/check_architecture.py --update-baseline
```

规则与文档一致：领域层不依赖上层/第三方 SDK、跨域不直接依赖对方 infrastructure、
域层不依赖 `interfaces`。**当前代码库 0 违规**，可以直接挂进 CI。
`tests/test_architecture_guard.py` 除了跑全量检查，还会插桩一条违规验证检查器
真的会报错（避免它变成又一个「看着有、实际不生效」的装饰器）。

### 5.6 跑 pytest

```bash
python -m pytest                    # 45 例
python -m pytest tests/test_history_compression.py -q
```

异步用例统一写成同步函数 + `asyncio.run(...)`：依赖里没有 `pytest-asyncio`，
不为写测试新增第三方插件。RAG 用例全部打桩（假 store + 假 embedding），
不依赖 Qdrant 服务与嵌入模型。

### 5.4 按调用契约补空壳

**不要凭名字猜该有什么。** 契约来自现存代码的调用点：

- `provider_chat_model.py:309-325` 的注释直接写明了缺哪两个方法
- `composition.py` 逐行写明了接口签名
- `event_sink.py` 写明了 4 个 opentelemetry 模块各自要提供什么

这样不会造出没人用的 API。

---

## 六、当前遗留

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| ~~1~~ ✅ 已修 | `domains/llm` 的 Azure / OpenAI Codex 坏映射 | 指向从未迁移的模块，访问即 `ModuleNotFoundError` | 4 条 llm 坏映射已摘除；**全项目坏映射已清零**（另有 `ToolRegistry` / `MemoryConfig` / `SessionMode` 等 11 条改指向真实定义，`reset_agent_invoke_count` 补齐实现） |
| ~~2~~ ✅ 已修 | 历史消息自动压缩无执行者 | 有状态标记与 `compressionThreshold` 配置，但没有「超阈值就压」的实现 | 新增 `HistoryCompressionService`（不切工具调用序列、模型摘要失败降级规则式、压不动就不压），并在每轮调 LLM 前调用；`AgentCompressionState` 空壳已补齐 |
| 3 | `domains/runtime` 命名歧义 | "runtime" 指编排运行时，与被解散的旧 `runtime/`（消息模型）同名不同义 | 改名 `orchestration` |
| 4 | `domains/tool` 顶层散落 | `registry.py` `base.py` `chain.py` 未分三层 | 补齐域内分层 |
| ~~5~~ ✅ 已修 | 无自动化测试套件 | 仅有 `e2e_tui_test.py` | 已建 `tests/` + `pytest.ini`，45 例覆盖三段式检索 / 8 种模式编排 / 历史压缩 / 架构守卫 |
| 6 | `domains/permission` 文件双份 | `cli_strategy.py` / `interrupt_manager.py` 在根目录与 `infrastructure/` 下各一份 | 确认 canonical 后去重 |
| 7 | `ReActAgent` 872 行 | 聚合根过大 | 按 `domains/agent` 的设计拆分 |
| ~~8~~ ✅ 已修 | 三条依赖约束未落地 | 靠人工约定维持 | 新增 `tools/check_architecture.py`（零第三方依赖、可接 CI），当前 **0 违规**；发现并纠正了两处真实反向依赖（`AgentExecutionStore` 端口下沉到 `domain/ports`、`SessionMode`/`ModelInfo` 下沉到 `configuration.domain`） |
| ~~9~~ ✅ 已删 | `interfaces/commands/implementation/mcp_selector.py` | 0 字节且**全项目零调用方** | 已删除：没有调用契约可依，补齐等于凭名字猜 API |
| 12 | 44 个 0 字节空壳 | 集中在 `hook`（13）、`agent`（9）、`runtime`（8）、`plugin`（4）等域 | 按调用契约补齐，或写明用途后保留 |
| ~~10~~ ✅ 已修 | `litellm` 未在 requirements 声明 | 计费能力静默降级 | 已列入 `requirements.txt` 的「可选依赖」段（连同 `fastmcp` / `datasets` 一并写明降级语义） |
| ~~11~~ ✅ 已修 | `billing` 域未接入主链路 | 已有 `BudgetTracker` 但无数据来源 | `TokenCounter` 新增 `BillingUsageSink` 协议并在每次真实用量时推送；billing 侧实现 `BudgetUsageSink`（`domains/billing/application/token_usage_bridge.py`），由 `ModelContextFactory` 按配置 `billing.enabled` 装配（默认关） |
| ~~13~~ ✅ 已修 | **惰性导出名与子模块同名**（地雷） | `file_lock` / `project_layout` 既是导出名又是本包子模块名。子模块一旦被导入，CPython 把模块对象绑到父包同名属性上，而 `__getattr__` 只在属性查找**失败**时才触发 —— 惰性导出被永久遮蔽，**进程启动即崩**：`TypeError: 'module' object is not callable` | 实现文件改名 `file_lock_impl.py` / `project_layout_impl.py` 以消除同名（两种用法对同一名字的要求互斥，急切绑定会反过来弄坏 `import a.b.c as x`）；新增架构规则 4 拦截此类新增 |
| ~~14~~ ✅ 已修 | **Qdrant 客户端 API 变更**（地雷） | `qdrant-client` 1.19 移除了 `client.search()`，代码无条件调用它，异常被 `except` 吞掉后返回空列表 —— **RAG 检索静默返回 0 条**：不报错、无外显日志、每次都是 0 条 | 按能力选择 `query_points()`（新版）/ `search()`（旧版），并把 swallow 处改为 `logger.exception` 留栈 |

### 两条地雷的共同点

都不是「功能没写」，而是**失败被吞掉**：一个被 CPython 的属性查找规则吞掉，一个被
`except Exception: return []` 吞掉。静态看代码完全正常，只有跑到特定导入顺序 /
特定依赖版本才炸。因此处理方式一致：

- 用例按**触发顺序**构造场景（先导入子模块、再经包属性调用），否则复现不出来；
- 规则进架构检查器（规则 4），新增即拦截；
- swallow 处至少留栈，让下一次 API 变更不再无声。

这两条的回归用例在 `tests/test_runtime_landmines.py`，**在修复前的代码上 6 项全红**。

---

## 七、备份与回滚

改代码前建议对涉及的目录做快照：

```bash
cd /d/AI/AIProject
mkdir -p _backup_<用途>_<日期>
cp -r RabbitAgent/<目标目录> _backup_<用途>_<日期>/
```

工程历史上有多个快照目录（`_backup_full_20261021` / `_backup_before_lazy` /
`_backup_e2e_before` / `_backup_merge_cli_20261021`）可参考其组织方式。
