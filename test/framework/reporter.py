# -*- coding: utf-8 -*-
"""验证结果输出：JSON / Markdown / HTML / CSV。

所有结果写入 test/results/，文件名带时间戳，支持多次运行对比（指南 5.2 门禁 5）。
"""

from __future__ import annotations

import csv
import datetime as _dt
import html
import json
import pathlib
from typing import Any, Iterable

from framework.case import (
    LAYER_NAMES,
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_SKIP,
    OUTCOME_XFAIL,
    CaseMeta,
)
from framework.scoring import gate_decision

_OUTCOME_LABEL = {
    OUTCOME_PASS: "通过",
    OUTCOME_FAIL: "失败",
    OUTCOME_SKIP: "不适用",
    OUTCOME_XFAIL: "已知缺陷",
    OUTCOME_ERROR: "异常",
}

_OUTCOME_ICON = {
    OUTCOME_PASS: "✅",
    OUTCOME_FAIL: "❌",
    OUTCOME_SKIP: "⏭️",
    OUTCOME_XFAIL: "⚠️",
    OUTCOME_ERROR: "💥",
}


def _now() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def write_all(
    metas: Iterable[CaseMeta],
    evaluation: dict[str, Any],
    out_dir: str | pathlib.Path,
    *,
    stamp: str | None = None,
) -> dict[str, pathlib.Path]:
    """输出四种格式的验证结果，返回路径字典。"""
    stamp = stamp or _now()
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    metas = sorted(metas, key=lambda m: (m.layer, m.guide_id, m.nodeid))

    paths = {
        "json": out / f"验证结果_{stamp}.json",
        "markdown": out / f"验证报告_{stamp}.md",
        "html": out / f"验证报告_{stamp}.html",
        "csv": out / f"验收矩阵_{stamp}.csv",
    }

    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "guide": "Agent 智能体完整验证指南 v1.0",
        "evaluation": evaluation,
        "conclusion": gate_decision(evaluation),
        "cases": [m.as_dict() for m in metas],
    }
    paths["json"].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    paths["markdown"].write_text(_render_markdown(metas, evaluation), encoding="utf-8")
    paths["html"].write_text(_render_html(metas, evaluation), encoding="utf-8")

    with paths["csv"].open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "编号", "层", "层名称", "维度", "用例标题", "严重级别",
            "阈值", "判定标准", "反例", "用例类别", "结果", "耗时(s)", "详情",
        ])
        for m in metas:
            writer.writerow([
                m.guide_id, m.layer, LAYER_NAMES.get(m.layer, m.layer),
                m.dimension, m.title, m.severity, m.threshold,
                m.criterion, m.counter_example, "/".join(m.tags),
                _OUTCOME_LABEL.get(m.outcome, m.outcome),
                f"{m.duration:.3f}", m.message.replace("\n", " ")[:300],
            ])

    _write_latest_pointer(out, paths, evaluation)
    prune_history(out)
    return paths


def _write_latest_pointer(
    out: pathlib.Path, paths: dict[str, pathlib.Path], evaluation: dict[str, Any]
) -> None:
    latest = {
        "json": paths["json"].name,
        "markdown": paths["markdown"].name,
        "html": paths["html"].name,
        "csv": paths["csv"].name,
        "total_score": evaluation.get("total_score"),
        "gate_passed": evaluation.get("gate_passed"),
        "verdict": evaluation.get("verdict"),
    }
    (out / "latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


#: 结果目录中每种格式最多保留的历史份数（0 表示不清理）
KEEP_HISTORY = 10


def prune_history(out: pathlib.Path, keep: int = KEEP_HISTORY) -> list[pathlib.Path]:
    """清理过期的历史结果，只保留最近 keep 份（每种格式分别计算）。

    这样 results/ 目录不会随运行次数无限膨胀，同时保留版本对比所需的历史。
    """
    if keep <= 0:
        return []
    removed: list[pathlib.Path] = []
    patterns = ("验证结果_*.json", "验证报告_*.md", "验证报告_*.html", "验收矩阵_*.csv")
    for pattern in patterns:
        files = sorted(
            (p for p in out.glob(pattern) if p.is_file()),
            key=lambda p: p.name,
            reverse=True,
        )
        for stale in files[keep:]:
            try:
                stale.unlink()
                removed.append(stale)
            except OSError:
                pass
    return removed


def load_latest_baseline(out_dir: str | pathlib.Path) -> dict[str, Any] | None:
    """读取上一次运行的评估结果，用于「相对上一版本无退化」判定。"""
    latest = pathlib.Path(out_dir) / "latest.json"
    if not latest.exists():
        return None
    try:
        info = json.loads(latest.read_text(encoding="utf-8"))
        prev = pathlib.Path(out_dir) / info["json"]
        if not prev.exists():
            return None
        return json.loads(prev.read_text(encoding="utf-8")).get("evaluation")
    except Exception:
        return None


# ── Markdown ────────────────────────────────────────────────────
def _render_markdown(metas: list[CaseMeta], ev: dict[str, Any]) -> str:
    s = ev["summary"]
    lines: list[str] = []
    a = lines.append

    a("# RabbitAgent 智能体验证报告")
    a("")
    a(f"> 依据《Agent 智能体完整验证指南》v1.0| 生成时间 {_dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    a("")
    a(f"## 结论：{ev['verdict']}")
    a("")
    a(f"**{gate_decision(ev)}**")
    a("")
    a(f"- 加权总分：**{ev['total_score']}** / 100（门禁要求 ≥ 80）")
    a(f"- 计分用例：{s['scored_total']} 条 → 通过 {s['passed']}、失败 {s['failed']}，通过率 {s['pass_rate']}%")
    a(f"- P0 用例：{s['p0_total']} 条 → 通过 {s['p0_passed']}、失败 {s['p0_failed']}、未判定 {s['p0_unscored']}")
    a(f"- 不适用（需外部服务/真实 LLM）：{s['skipped']} 条| 已知缺陷（xfail）：{s['xfailed']} 条")
    a("")

    a("## 一、分层得分")
    a("")
    a("| 层 | 名称 | 权重 | 计分 | 通过 | 失败 | 通过率 | P0 通过率 | 加权贡献 |")
    a("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for lid in sorted(ev["layers"]):
        b = ev["layers"][lid]
        contrib = round(b["pass_rate"] * b["weight"] / 100.0, 2)
        a(f"| {lid} | {b['name']} | {b['weight']}% | {b['total']} | {b['passed']} | {b['failed']} "
          f"| {b['pass_rate']}% | {b['p0_pass_rate']}% | {contrib} |")
    a(f"| — | **合计** | 100% | **{s['scored_total']}** | **{s['passed']}** | **{s['failed']}** "
      f"| — | — | **{ev['total_score']}** |")
    a("")

    a("## 二、发布门禁（指南 5.2）")
    a("")
    a("| 门禁 | 规则 | 实测 | 阈值 | 判定 | 说明 |")
    a("|---|---|---|---|---|---|")
    for g in ev["gates"]:
        mark = "✅ 通过" if g["passed"] else "❌ 未通过"
        a(f"| {g['id']} | {g['rule']} | {g['actual']} | {g['threshold']} | {mark} | {g['detail']} |")
    a("")

    failed = [m for m in metas if m.outcome in (OUTCOME_FAIL, OUTCOME_ERROR)]
    if failed:
        a("## 三、失败用例明细")
        a("")
        a("| 编号 | 严重级别 | 用例 | 失败原因 |")
        a("|---|---|---|---|")
        for m in sorted(failed, key=lambda x: (x.severity != "P0", x.layer, x.guide_id)):
            reason = m.message.replace("\n", " ").replace("|", "\\|")[:220]
            a(f"| {m.guide_id} | {m.severity} | {m.title} | {reason} |")
        a("")

    p0_bad = [m for m in failed if m.severity == "P0"]
    if p0_bad:
        a("### P0 一票否决项（必须修复后方可发布）")
        a("")
        for m in p0_bad:
            a(f"- **{m.guide_id} {m.title}** — {m.message.splitlines()[0][:200]}")
        a("")

    a("## 四、全部用例明细")
    a("")
    a("| 编号 | 层 | 严重级别 | 用例 | 判定标准 | 结果 |")
    a("|---|---|---|---|---|---|")
    for m in metas:
        crit = m.criterion.replace("\n", " ").replace("|", "\\|")
        a(f"| {m.guide_id} | {m.layer} | {m.severity} | {m.title} | {crit} "
          f"| {_OUTCOME_ICON.get(m.outcome, '')} {_OUTCOME_LABEL.get(m.outcome, m.outcome)} |")
    a("")
    return "\n".join(lines)


# ── HTML ────────────────────────────────────────────────────────
_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:32px;background:#f6f7f9;color:#1f2328;
     font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.6}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px}h2{font-size:19px;margin:32px 0 12px;
   padding-bottom:8px;border-bottom:1px solid #d8dee4}
.sub{color:#656d76;font-size:13px;margin-bottom:20px}
.card{background:#fff;border:1px solid #d8dee4;border-radius:10px;padding:20px 24px;margin-bottom:18px}
.verdict{font-size:17px;font-weight:600;padding:14px 18px;border-radius:8px;margin:16px 0}
.pass{background:#dafbe1;border-left:4px solid #1a7f37;color:#0f5329}
.fail{background:#ffebe9;border-left:4px solid #cf222e;color:#82071e}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:18px 0}
.kpi{background:#f6f8fa;border:1px solid #d8dee4;border-radius:8px;padding:14px;text-align:center}
.kpi .n{font-size:26px;font-weight:700;display:block}
.kpi .l{font-size:12px;color:#656d76;margin-top:3px}
table{width:100%;border-collapse:collapse;background:#fff;font-size:13px}
th,td{padding:9px 11px;border:1px solid #d8dee4;text-align:left;vertical-align:top}
th{background:#f6f8fa;font-weight:600;white-space:nowrap}
tbody tr:nth-child(even){background:#fafbfc}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}
.t-P0{background:#cf222e;color:#fff}.t-P1{background:#bf8700;color:#fff}.t-P2{background:#6e7781;color:#fff}
.bar{height:7px;background:#e6eaef;border-radius:4px;overflow:hidden;min-width:70px}
.bar>i{display:block;height:100%;border-radius:4px}
.g{background:#1a7f37}.y{background:#bf8700}.r{background:#cf222e}
code{background:#f0f2f5;padding:1px 5px;border-radius:4px;font-size:12px;
     font-family:Consolas,Monaco,monospace}
.msg{color:#82071e;font-size:12px;max-width:420px;word-break:break-word}
.muted{color:#656d76}
"""


def _render_html(metas: list[CaseMeta], ev: dict[str, Any]) -> str:
    s = ev["summary"]
    gate_ok = ev["gate_passed"]
    e = html.escape

    def bar(rate: float) -> str:
        cls = "g" if rate >= 95 else ("y" if rate >= 80 else "r")
        return f'<div class="bar"><i class="{cls}" style="width:{min(rate,100)}%"></i></div>'

    parts: list[str] = []
    a = parts.append
    a("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    a("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    a("<title>RabbitAgent 智能体验证报告</title>")
    a(f"<style>{_CSS}</style></head><body><div class='wrap'>")

    a("<h1>RabbitAgent 智能体验证报告</h1>")
    a(f"<div class='sub'>依据《Agent 智能体完整验证指南》v1.0 · 生成于 {_dt.datetime.now():%Y-%m-%d %H:%M:%S}</div>")

    cls = "pass" if gate_ok else "fail"
    a(f"<div class='verdict {cls}'>{e(gate_decision(ev))}</div>")

    a("<div class='kpis'>")
    for n, l in [
        (ev["total_score"], "加权总分"),
        (s["pass_rate"], "通过率 %"),
        (f"{s['p0_passed']}/{s['p0_total']}", "P0 通过"),
        (s["scored_total"], "计分用例"),
        (s["failed"], "失败"),
        (s["skipped"], "不适用"),
    ]:
        a(f"<div class='kpi'><span class='n'>{e(str(n))}</span><span class='l'>{e(l)}</span></div>")
    a("</div>")

    a("<h2>一、分层得分</h2><div class='card'><table><thead><tr>"
      "<th>层</th><th>名称</th><th>权重</th><th>计分</th><th>通过</th><th>失败</th>"
      "<th>通过率</th><th>P0</th><th>加权贡献</th></tr></thead><tbody>")
    for lid in sorted(ev["layers"]):
        b = ev["layers"][lid]
        contrib = round(b["pass_rate"] * b["weight"] / 100.0, 2)
        a(f"<tr><td><b>{lid}</b></td><td>{e(b['name'])}</td><td class='num'>{b['weight']}%</td>"
          f"<td class='num'>{b['total']}</td><td class='num'>{b['passed']}</td>"
          f"<td class='num'>{b['failed']}</td><td class='num'>{b['pass_rate']}% {bar(b['pass_rate'])}</td>"
          f"<td class='num'>{b['p0_pass_rate']}%</td><td class='num'><b>{contrib}</b></td></tr>")
    a(f"<tr style='font-weight:600'><td colspan='3'>合计</td><td class='num'>{s['scored_total']}</td>"
      f"<td class='num'>{s['passed']}</td><td class='num'>{s['failed']}</td>"
      f"<td colspan='2'></td><td class='num'>{ev['total_score']}</td></tr>")
    a("</tbody></table></div>")

    a("<h2>二、发布门禁</h2><div class='card'><table><thead><tr>"
      "<th>门禁</th><th>规则</th><th>实测</th><th>阈值</th><th>判定</th><th>说明</th>"
      "</tr></thead><tbody>")
    for g in ev["gates"]:
        mark = "✅ 通过" if g["passed"] else "❌ 未通过"
        a(f"<tr><td><b>{g['id']}</b></td><td>{e(g['rule'])}</td><td>{e(str(g['actual']))}</td>"
          f"<td class='muted'>{e(g['threshold'])}</td><td><b>{mark}</b></td>"
          f"<td class='muted'>{e(g['detail'])}</td></tr>")
    a("</tbody></table></div>")

    failed = [m for m in metas if m.outcome in (OUTCOME_FAIL, OUTCOME_ERROR)]
    if failed:
        a(f"<h2>三、失败用例明细（{len(failed)}）</h2><div class='card'><table><thead><tr>"
          "<th>编号</th><th>级别</th><th>用例</th><th>失败原因</th></tr></thead><tbody>")
        for m in sorted(failed, key=lambda x: (x.severity != "P0", x.layer, x.guide_id)):
            reason = e(m.message.replace("\n", " ")[:300])
            a(f"<tr><td><code>{e(m.guide_id)}</code></td>"
              f"<td><span class='tag t-{m.severity}'>{m.severity}</span></td>"
              f"<td>{e(m.title)}</td><td class='msg'>{reason}</td></tr>")
        a("</tbody></table></div>")

    a(f"<h2>四、全部用例明细（{len(metas)}）</h2><div class='card'><table><thead><tr>"
      "<th>编号</th><th>层</th><th>级别</th><th>用例</th><th>判定标准</th><th>结果</th>"
      "</tr></thead><tbody>")
    for m in metas:
        a(f"<tr><td><code>{e(m.guide_id)}</code></td><td>{m.layer}</td>"
          f"<td><span class='tag t-{m.severity}'>{m.severity}</span></td>"
          f"<td>{e(m.title)}</td><td class='muted'>{e(m.criterion[:150])}</td>"
          f"<td>{_OUTCOME_ICON.get(m.outcome,'')} {e(_OUTCOME_LABEL.get(m.outcome, m.outcome))}</td></tr>")
    a("</tbody></table></div></div></body></html>")
    return "".join(parts)