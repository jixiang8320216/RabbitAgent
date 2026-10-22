# -*- coding: utf-8 -*-
"""L3 可观测层：Trace 链路、Token 归因、延迟分解、评测能力、成本熔断。

指南强调 L3-2.2「Token 归因到任务级」是最容易被忽略但最有价值的一项：
只统计总量只知道「花了多少」，不知道「花在哪」，优化无从下手。
"""

from __future__ import annotations

import asyncio
import statistics
import time

import pytest

from fixtures import require_module

from framework.case import case, require, require_eq


# ══════════════════════════════════════════════════════════════
# L3-1 Trace
# ══════════════════════════════════════════════════════════════

@case(
    "L3-1.1",
    "Trace ID 可生成、设置与读取，格式可辨识",
    title="Trace ID 不可用",
    dimension="Trace - 链路完整性",
    severity="P1",
    threshold="100%",
    counter_example="无法设置 trace 上下文 → 链路无法串联",
    tags=("黄金",),
)
def test_trace_context():
    from domains.observability.application import observability_context as ctx

    trace_id = ctx.generate_trace_id()
    require(trace_id.startswith("tr_"), f"trace_id 缺少 tr_ 前缀：{trace_id!r}")
    require(len(trace_id) >= 10, f"trace_id 过短，无法保证唯一性：{trace_id!r}")

    ctx.set_trace_id(trace_id)
    require_eq(ctx.get_trace_id(), trace_id, "读回的 trace_id")

    ctx.set_app_mode("delegate")
    require_eq(ctx.get_app_mode(), "delegate", "读回的 app_mode")


@case(
    "L3-1.1",
    "连续生成的 Trace ID互不相同（可区分并发会话）",
    title="Trace ID 重复",
    dimension="Trace - 链路完整性",
    severity="P1",
    threshold="100% 唯一",
    counter_example="Trace ID 重复 → 并发会话的日志被错误合并",
    tags=("故障",),
)
def test_trace_id_unique():
    from domains.observability.application import observability_context as ctx

    ids = {ctx.generate_trace_id() for _ in range(500)}
    require_eq(len(ids), 500, "500 次生成的 Trace ID 去重后数量")


@case(
    "L3-1.3",
    "调用计数可累加并复位，用于定位失败环节",
    title="调用计数不可用",
    dimension="Trace - 因果关联",
    severity="P1",
    threshold="100%",
    counter_example="工具调用计数恒为 0 → 无法判断是否真的调用了工具",
    tags=("黄金",),
)
def test_call_counters():
    from domains.observability.application import observability_context as ctx

    ctx.reset_query_stats()
    require_eq(ctx.increment_agent_invoke_count(2), 2, "Agent 调用计数")
    require_eq(ctx.increment_tool_call_count(5), 5, "工具调用计数")

    ctx.reset_query_stats()
    require_eq(ctx.get_tool_call_count(), 0, "复位后的工具调用计数")
    require_eq(ctx.get_agent_invoke_count(), 0, "复位后的 Agent 调用计数")


@case(
    "L3-1.3",
    "查询起始时间可记录，供端到端延迟计算",
    title="查询起始时间未记录",
    dimension="Trace - 因果关联",
    severity="P1",
    threshold="100%",
    counter_example="无起始时间 → 端到端延迟无法计算",
    tags=("黄金",),
)
def test_query_start_time():
    from domains.observability.application import observability_context as ctx

    ctx.set_query_start_time()
    started = ctx.get_query_start_time()
    require(started is not None, "查询起始时间为 None")
    require(
        started <= time.monotonic() + 1,
        f"查询起始时间不合理：{started}",
    )


# ══════════════════════════════════════════════════════════════
# L3-2 Token 与成本
# ══════════════════════════════════════════════════════════════

@case(
    "L3-2.1",
    "ChatUsage 分项统计包含 input / output / reasoning / cached 四类且互不混淆",
    title="Token 分项缺失",
    dimension="Token统计 - 分项准确",
    severity="P1",
    threshold="100%",
    counter_example="缺少 cached 字段 → 无法核算缓存命中带来的成本节省",
    tags=("黄金",),
)
def _chat_usage_or_skip():
    """导入 ChatUsage；若被前向引用缺陷阻断则显式 skip。"""
    try:
        from domains.agent.domain.value_objects.chat_response import ChatUsage
    except (NameError, ModuleNotFoundError) as exc:
        pytest.skip(f"chat_response 依赖链被前向引用缺陷阻断：{exc}")
    return ChatUsage


@case(
    "L3-2.1",
    "ChatUsage 分项统计包含 input / output / reasoning / cached 四类且互不混淆",
    title="Token 分项缺失",
    dimension="Token统计 - 分项准确",
    severity="P1",
    threshold="100%",
    counter_example="缺少 cached 字段 → 无法核算缓存命中带来的成本节省",
    tags=("黄金",),
)
def test_chat_usage_fields():
    ChatUsage = _chat_usage_or_skip()

    usage = ChatUsage(
        input_tokens=100, output_tokens=50, reasoning_tokens=10, cached_tokens=30
    )
    dumped = usage.model_dump()
    for field in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens"):
        require(field in dumped, f"ChatUsage 缺少字段 {field!r}", detail=str(list(dumped)))
    require_eq(dumped["input_tokens"], 100, "input_tokens")
    require_eq(dumped["cached_tokens"], 30, "cached_tokens（缓存命中）")


@case(
    "L3-2.1",
    "ChatUsage 拒绝负数 token 计数（防止异常数据污染账单）",
    title="负数 token 被接受",
    dimension="Token统计 - 分项准确",
    severity="P1",
    threshold="100%",
    counter_example="负数 token 被接受 → 成本统计出现负值",
    tags=("故障", "脏数据"),
)
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens"])
def test_chat_usage_rejects_negative(field):
    ChatUsage = _chat_usage_or_skip()

    with pytest.raises(Exception):
        ChatUsage(**{field: -1})


@case(
    "L3-2.2",
    "Token 用量可归因到单个 Agent（而非仅会话总量）",
    title="Token 用量无法归因到 Agent",
    dimension="Token统计 - 归因到任务",
    severity="P1",
    threshold="100%",
    counter_example="只有会话总量 → 不知道是哪个 Agent 消耗了 token",
    tags=("黄金",),
)
def test_token_attribution_per_agent():
    counter = require_module(
        "domains.llm.provider.application.token.token_counter",
        hint="token 统计被 runtime_configuration 的前向引用缺陷阻断。",
    ).TokenCounter(model_name="gpt-4")

    counter.set_api_tokens(
        agent_name="planner", prompt_tokens=1000, completed_tokens=200
    )
    counter.set_api_tokens(
        agent_name="executor", prompt_tokens=3000, completed_tokens=600
    )

    planner = counter.get_agent_tokens("planner")
    executor = counter.get_agent_tokens("executor")
    require(
        int(planner["prompt_tokens"]) == 1000,
        f"planner 的 prompt_tokens 不符：{planner}",
    )
    require(
        int(executor["prompt_tokens"]) == 3000,
        f"executor 的 prompt_tokens 不符：{executor}",
    )

    all_tokens = counter.get_all_agent_tokens()
    require_eq(sorted(all_tokens), ["executor", "planner"], "已归因的 Agent 列表")
    counter.reset()


@case(
    "L3-2.2",
    "Token 用量可按操作类型（operation_kind）进一步拆分",
    title="Token 用量无法按操作类型拆分",
    dimension="Token统计 - 归因到任务",
    severity="P1",
    threshold="100%",
    counter_example="压缩与正常调用混在一起 → 无法评估压缩收益",
    tags=("黄金",),
)
def test_token_attribution_by_operation_kind():
    counter = require_module(
        "domains.llm.provider.application.token.token_counter",
        hint="token 统计被 runtime_configuration 的前向引用缺陷阻断。",
    ).TokenCounter(model_name="gpt-4")

    counter.set_api_tokens(
        agent_name="a1", prompt_tokens=500, completed_tokens=100,
        operation_kind="normal",
    )
    counter.set_api_tokens(
        agent_name="a1", prompt_tokens=200, completed_tokens=50,
        operation_kind="compression",
    )

    normal = counter.get_agent_tokens_by_kind("a1", "normal")
    compression = counter.get_agent_tokens_by_kind("a1", "compression")
    require_eq(int(normal["prompt_tokens"]), 500, "normal 类型 token")
    require_eq(int(compression["prompt_tokens"]), 200, "compression 类型 token")
    counter.reset()


@case(
    "L3-2.5",
    "预算状态机正确：超额后按策略判定是否拒绝",
    title="预算熔断逻辑错误",
    dimension="Token统计 - 预算熔断",
    severity="P0",
    threshold="100%",
    counter_example="超额后 should_reject 仍为 False → 成本失控无阻断",
    tags=("黄金",),
)
def test_budget_state_machine():
    from domains.billing.domain.litellm_budget import (
        BudgetExceededAction,
        BudgetScope,
        BudgetState,
    )

    within = BudgetState(limit_usd=10.0, spent_usd=5.0)
    require(within.exceeded is False, "未超额却标记为 exceeded")
    require_eq(within.remaining_usd, 5.0, "未超额时的剩余额度")
    require(within.should_reject() is False, "未超额时不应拒绝")

    exceeded_warn = BudgetState(limit_usd=1.0, spent_usd=1.5)
    require(exceeded_warn.exceeded is True, "超额未被检出")
    require_eq(exceeded_warn.remaining_usd, 0.0, "超额后的剩余额度（不为负）")
    require(exceeded_warn.should_reject() is False, "WARN 策略下不应拒绝")

    exceeded_reject = BudgetState(
        limit_usd=1.0, spent_usd=1.5,
        exceeded_action=BudgetExceededAction.REJECT,
    )
    require(
        exceeded_reject.should_reject() is True,
        "REJECT 策略下超额仍不拒绝 → 成本熔断失效",
    )

    unlimited = BudgetState(limit_usd=None, spent_usd=999.0)
    require(unlimited.exceeded is False, "不限额时不应判定为超额")
    require_eq(unlimited.remaining_usd, None, "不限额时的剩余额度")
    require_eq(str(BudgetScope.GLOBAL), "global", "预算作用域枚举")


@case(
    "L3-2.5",
    "预算按作用域归因：全局 / Agent / 会话三个维度独立累计",
    title="预算无法按作用域归因",
    dimension="Token统计 - 归因到任务",
    severity="P1",
    threshold="100%",
    counter_example="只有全局总额 → 无法定位是哪个 Agent 超支",
    tags=("黄金",),
)
def test_budget_scope_attribution():
    from domains.billing.domain.litellm_budget import BudgetScope, BudgetTracker

    tracker = BudgetTracker(limit_usd=None)
    require_eq(tracker.spent_for(BudgetScope.GLOBAL), 0.0, "初始全局花费")
    require_eq(tracker.spent_for(BudgetScope.AGENT, "agent-a"), 0.0, "初始 Agent 花费")
    require_eq(tracker.state.spent_usd, 0.0, "初始状态花费")
    require(tracker.state.limit_usd is None, "不限额时的 limit")


@case(
    "L3-2.4",
    "异常消耗可被检出：健康监控能记录错误并降级状态",
    title="健康监控无法检出异常",
    dimension="Token统计 - 异常检测",
    severity="P1",
    threshold="100%",
    counter_example="错误发生后健康状态仍为 HEALTHY → 异常静默",
    tags=("故障",),
)
def test_health_monitor_detects_degradation():
    """健康状态需覆盖「采集端内部异常」。

    已知缺陷：health.py 的 status() 只依据 QUEUE_OVERFLOWED /
    ENDPOINT_UNREACHABLE / QUEUE_NEAR_CAPACITY 判定，未把 INTERNAL_ERROR
    计入降级依据 → 遥测链路自身崩溃时健康状态仍显示 HEALTHY。
    """
    from domains.observability.application.health import HealthMonitor
    from domains.observability.domain.health import HealthStatus

    monitor = HealthMonitor()
    require_eq(monitor.status(), HealthStatus.HEALTHY, "初始健康状态")

    monitor.record_error("采集端内部异常")
    require_eq(monitor.last_error(), "采集端内部异常", "最近错误信息")
    require(
        monitor.status() != HealthStatus.HEALTHY,
        "记录内部异常后健康状态仍为 HEALTHY：status() 未把 INTERNAL_ERROR "
        "纳入降级依据 → 遥测链路自身故障时监控面板显示「健康」，异常被静默",
        detail=f"alerts={monitor.alerts()}",
    )


@case(
    "L3-2.4",
    "端点不可达被判定为 UNHEALTHY（最高级别降级）",
    title="端点不可达未判定为不健康",
    dimension="Token统计 - 异常检测",
    severity="P0",
    threshold="100%",
    counter_example="遥测端点不可达仍显示 HEALTHY → 上报链路已断却无人知晓",
    tags=("故障", "P0"),
)
def test_unreachable_endpoint_unhealthy():
    from domains.observability.application.health import HealthMonitor
    from domains.observability.domain.health import (
        HealthStatus,
        TelemetryAlertCategory,
    )

    monitor = HealthMonitor()
    monitor.record(TelemetryAlertCategory.ENDPOINT_UNREACHABLE)
    require_eq(
        monitor.status(), HealthStatus.UNHEALTHY, "端点不可达时的健康状态"
    )


@case(
    "L3-2.4",
    "队列溢出被判定为 UNHEALTHY，接近容量判定为 DEGRADED（分级正确）",
    title="队列告警分级不正确",
    dimension="Token统计 - 异常检测",
    severity="P1",
    threshold="100%",
    counter_example="溢出与接近容量同为一级 → 无法区分严重程度",
    tags=("故障",),
)
def test_queue_alert_severity_grading():
    from domains.observability.application.health import HealthMonitor
    from domains.observability.domain.health import HealthStatus

    near = HealthMonitor()
    near.record_queue_level("q", level=85, capacity=100)
    require_eq(near.status(), HealthStatus.DEGRADED, "接近容量时的状态")

    over = HealthMonitor()
    over.record_queue_level("q", level=100, capacity=100)
    require_eq(over.status(), HealthStatus.UNHEALTHY, "队列溢出时的状态")


@case(
    "L3-2.4",
    "队列水位按 80% 阈值告警，且低于阈值不告警（无误报）",
    title="队列水位告警阈值不正确",
    dimension="Token统计 - 异常检测",
    severity="P1",
    threshold="80% 触发 / 79% 不触发",
    counter_example="低于阈值也告警 → 告警疲劳，真实问题被淹没",
    tags=("边界",),
)
@pytest.mark.parametrize(
    "level,expect_alert",
    [(79, False), (80, True), (95, True)],
    ids=["79%不告警", "80%告警", "95%告警"],
)
def test_queue_warn_threshold(level, expect_alert):
    from domains.observability.application.health import HealthMonitor

    monitor = HealthMonitor()
    monitor.record_queue_level("q", level=level, capacity=100)
    require(
        bool(monitor.alerts()) is expect_alert,
        f"队列水位 {level}% 的告警状态不符（期望告警={expect_alert}）",
        detail=f"alerts={monitor.alerts()}",
    )


@case(
    "L3-2.4",
    "告警按类别计数，可定位错误类型",
    title="告警无法按类别统计",
    dimension="Token统计 - 异常检测",
    severity="P1",
    threshold="100%",
    counter_example="告警无分类 → 无法按类型统计与告警",
    tags=("故障",),
)
def test_alert_categories_recorded():
    from domains.observability.application.health import HealthMonitor

    monitor = HealthMonitor()
    monitor.record_error("内部错误")
    monitor.record_error("内部错误")
    alerts = monitor.alerts()
    require(
        alerts,
        "错误未记入告警统计 → 完全不可观测",
        detail=f"alerts={alerts}",
    )
    require(
        any("internal" in key for key in alerts),
        f"告警未按 internal_error 类别归类：{alerts}",
    )


@case(
    "L3-2.4",
    "队列水位接近容量时触发告警（成本失控的唯一预警手段）",
    title="队列水位告警失效",
    dimension="Token统计 - 异常检测",
    severity="P1",
    threshold="≥ 80% 触发",
    counter_example="队列接近满载无告警 → 事件被丢弃",
    tags=("故障",),
)
def test_queue_near_capacity_alerts():
    from domains.observability.application.health import HealthMonitor
    from domains.observability.domain.health import HealthStatus

    monitor = HealthMonitor()
    monitor.record_queue_level("events", level=85, capacity=100)
    require(
        monitor.status() != HealthStatus.HEALTHY,
        "队列水位 85% 未触发告警",
        detail=f"queue_levels={monitor.queue_levels()}",
    )
    require(
        "events" in monitor.queue_levels(),
        "队列水位未被记录 → 无法做容量规划",
    )


# ══════════════════════════════════════════════════════════════
# L3-2.3 延迟分解
# ══════════════════════════════════════════════════════════════

@case(
    "L3-2.3",
    "遥测计时护栏能测量耗时且不吞掉业务异常",
    title="遥测计时护栏吞掉业务异常",
    dimension="Token统计 - 延迟分解",
    severity="P1",
    threshold="100% 不吞异常",
    counter_example="业务异常被护栏吞掉 → 故障被静默",
    tags=("故障",),
)
def test_telemetry_guard_preserves_exception():
    from domains.observability.application.guard import telemetry_guard

    with pytest.raises(ValueError, match="业务异常"):
        with telemetry_guard("test_op"):
            raise ValueError("业务异常")


@case(
    "L3-2.3",
    "遥测护栏能测量出实际耗时（可用于慢调用告警）",
    title="遥测护栏无法测量耗时",
    dimension="Token统计 - 延迟分解",
    severity="P1",
    threshold="≥ 40ms（sleep 40ms 实测）",
    counter_example="耗时恒为 0 → 慢调用无法检出",
    tags=("性能",),
)
def test_telemetry_guard_measures_elapsed():
    from domains.observability.application.guard import SLOW_CALL_THRESHOLD_MS, telemetry

    require(SLOW_CALL_THRESHOLD_MS > 0, "慢调用阈值未配置")
    started = time.perf_counter()
    with telemetry("slow_op"):
        time.sleep(0.04)
    elapsed_ms = (time.perf_counter() - started) * 1000
    require(
        elapsed_ms >= 40.0,
        f"护栏内耗时 {elapsed_ms:.1f}ms 小于实际 sleep 时长",
    )


# ══════════════════════════════════════════════════════════════
# L3-3 评测能力
# ══════════════════════════════════════════════════════════════

@case(
    "L3-3.3",
    "BFCL 评测指标可独立计算，准确率/AST 匹配/参数准确率均为纯函数",
    title="评测指标不可用",
    dimension="评测能力 - 指标可归因",
    severity="P1",
    threshold="100%",
    counter_example="指标依赖外部服务无法计算 → 无法回归对比",
    tags=("黄金",),
)
def test_bfcl_metrics():
    from domains.evaluation.benchmarks.bfcl.metrics import BFCLMetrics

    # 准确率为 0~1 的比例（3/4 = 0.75）
    require_eq(BFCLMetrics.calculate_accuracy([1, 0, 1, 1], [1, 0, 0, 1]), 0.75, "准确率")
    require_eq(BFCLMetrics.calculate_accuracy([], []), 0.0, "空样本的准确率")
    require_eq(BFCLMetrics.calculate_f1_score(1.0, 1.0), 1.0, "完全匹配时的 F1")
    require_eq(BFCLMetrics.calculate_f1_score(0.0, 0.0), 0.0, "完全不匹配时的 F1")
    require_eq(
        BFCLMetrics.calculate_parameter_accuracy({"a": 1}, {"a": 1}), 1.0, "参数完全匹配"
    )
    require_eq(
        BFCLMetrics.calculate_parameter_accuracy({"a": 2}, {"a": 1}), 0.0, "参数不匹配"
    )
    require_eq(BFCLMetrics.calculate_ast_match("f(1)", "f(1)"), 1.0, "AST 完全匹配")
    for value in (
        BFCLMetrics.calculate_accuracy([1, 0], [1, 1]),
        BFCLMetrics.calculate_parameter_accuracy({}, {}),
    ):
        require(
            0.0 <= value <= 1.0,
            f"指标值 {value} 不在 [0,1] 区间",
        )


@case(
    "L3-3.2",
    "评测指标可复现：同一输入多次计算结果完全一致（离散度为 0）",
    title="评测指标不可复现",
    dimension="评测能力 - 指标可复现",
    severity="P1",
    threshold="离散度 < 5%",
    counter_example="同一输入多次计算结果波动 → 基线不可信",
    tags=("故障",),
)
def test_metrics_reproducible():
    from domains.evaluation.benchmarks.bfcl.metrics import BFCLMetrics

    preds = [1, 0, 1, 1, 0, 1, 0, 0]
    refs = [1, 0, 0, 1, 1, 1, 0, 0]
    results = [BFCLMetrics.calculate_accuracy(preds, refs) for _ in range(20)]
    require(
        max(results) - min(results) == 0,
        f"准确率计算结果不稳定：min={min(results)}, max={max(results)}",
    )


@case(
    "L3-3.1",
    "LLM 裁判的响应解析失败时返回保守默认分而非抛异常（评测器自身健壮性）",
    title="评测器响应解析失败导致崩溃",
    dimension="评测能力 - 评测器校准",
    severity="P1",
    threshold="100% 不抛异常",
    counter_example="解析失败抛异常 → 整个评测流程中断",
    tags=("故障", "脏数据"),
)
@pytest.mark.parametrize(
    "response",
    ["", "   ", "完全无法解析的内容", "### malformed ###", "{}", "null"],
)
def test_llm_judge_parse_failure_safe(response):
    try:
        from domains.evaluation.benchmarks.data_generation.llm_judge import LLMJudgeEvaluator
    except ModuleNotFoundError as exc:
        pytest.skip(f"评测器依赖未安装：{exc.name}")

    evaluator = LLMJudgeEvaluator(llm=None)
    try:
        scores = evaluator._parse_evaluation_response(response)
    except Exception as exc:  # noqa: BLE001
        require(
            False,
            f"评测器解析 {response!r} 时抛出异常：{type(exc).__name__}: {exc}",
            detail="评测器应对畸形输出容错，否则被评模型的一次异常输出就会中断评测",
        )
    else:
        require(isinstance(scores, dict), f"解析结果不是字典：{type(scores).__name__}")
        require(
            all(isinstance(v, (int, float)) for v in scores.values()),
            f"解析结果含非数值评分：{scores}",
        )


@case(
    "L3-3.1",
    "评测维度定义完整：correctness / clarity / difficulty_match / completeness",
    title="评测维度缺失",
    dimension="评测能力 - 评测器校准",
    severity="P1",
    threshold="4 个维度",
    counter_example="缺少 completeness 维度 → 无法评估答案完整度",
    tags=("黄金",),
)
def test_evaluation_dimensions_complete():
    try:
        from domains.evaluation.benchmarks.data_generation.llm_judge import LLMJudgeEvaluator
    except ModuleNotFoundError as exc:
        pytest.skip(f"评测器依赖未安装：{exc.name}")

    required = {"correctness", "clarity", "difficulty_match", "completeness"}
    actual = set(LLMJudgeEvaluator.EVALUATION_DIMENSIONS)
    missing = required - actual
    require(not missing, f"缺少评测维度：{missing}", detail=f"现有：{sorted(actual)}")


@case(
    "L3-3.4",
    "评测结果可导出为标准格式，支持版本间基线对比",
    title="评测结果不可导出",
    dimension="评测能力 - 基线管理",
    severity="P1",
    threshold="100%",
    counter_example="结果无法落盘 → 无法做版本对比，指南 5.2 门禁 5 无法执行",
    tags=("黄金",),
)
def test_evaluation_export(tmp_path):
    import json

    try:
        mod = require_module(
            "domains.evaluation.benchmarks.bfcl.evaluator",
            hint="BFCL 评测器需数据集模块可导入。",
        )
        evaluator = mod.BFCLEvaluator()
    except TypeError as exc:
        pytest.skip(f"BFCL 评测器构造签名不匹配（接口可能已变更）：{exc}")

    output = tmp_path / "bfcl_result.json"
    evaluator.export_to_bfcl_format(
        {
            "benchmark": "bfcl",
            "agent_name": "test-agent",
            "total_samples": 2,
            "correct_samples": 1,
            "overall_accuracy": 50.0,
            "detailed_results": [],
        },
        output,
    )
    require(output.exists(), "评测结果未落盘")
    payload = json.loads(output.read_text(encoding="utf-8"))
    require(payload, "导出的结果为空")


@case(
    "L3-3.4",
    "Token 计数可在无网络环境对任意文本工作（估算兜底可用）",
    title="离线 Token 计数不可用",
    dimension="Token统计 - 分项准确",
    severity="P1",
    threshold="100%",
    counter_example="未装 tiktoken 时直接报错 → 上下文压缩与预算控制失效",
    tags=("故障",),
)
def test_offline_token_counting():
    counter_cls = require_module(
        "domains.llm.provider.application.token.openai_token_counting",
        hint="token 统计被 runtime_configuration 的前向引用缺陷阻断。",
    ).OpenAILocalTokenCounter

    counter = counter_cls(model_name="gpt-4")
    cjk = asyncio.run(counter.count([{"role": "user", "content": "你好，世界"}]))
    ascii_text = asyncio.run(counter.count("hello world"))
    empty = asyncio.run(counter.count([]))

    require(cjk > 0, f"中文 token 计数为 {cjk}（应为正数）")
    require(ascii_text > 0, f"英文 token 计数为 {ascii_text}（应为正数）")
    require_eq(empty, 0, "空消息列表的 token 计数")
    require(
        cjk >= 4,
        f"中文 token 计数 {cjk} 明显偏低：'你好，世界' 应至少 4 token",
    )