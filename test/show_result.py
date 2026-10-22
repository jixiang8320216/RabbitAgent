# -*- coding: utf-8 -*-
"""查看最近一次验证结果的摘要（跨平台，避免 shell 转义问题）。"""

from __future__ import annotations

import json
import pathlib
import sys

RESULTS = pathlib.Path(__file__).resolve().parent / "results"


def main() -> int:
    latest = RESULTS / "latest.json"
    if not latest.exists():
        print("尚无验证结果，请先运行：python test/run_verification.py")
        return 1

    info = json.loads(latest.read_text(encoding="utf-8"))
    full = json.loads((RESULTS / info["json"]).read_text(encoding="utf-8"))
    ev = full["evaluation"]
    s = ev["summary"]

    print("=" * 74)
    print(f"  {full['conclusion']}")
    print("=" * 74)
    print(f"  加权总分{ev['total_score']:>6} / 100（门禁要求 ≥ 80）")
    print(f"  通过率        {s['pass_rate']:>6}%   计分 {s['scored_total']} 条"
          f"（通过 {s['passed']} / 失败 {s['failed']}）")
    print(f"  P0 一票否决   {s['p0_passed']:>6} / {s['p0_total']}"
          f"（失败 {s['p0_failed']}）")
    print(f"  不适用/已知   {s['skipped']:>6} / {s['xfailed']}")
    print()
    print("  分层得分")
    print("  " + "-" * 70)
    print(f"  {'层':<5}{'名称':<14}{'权重':>6}{'计分':>7}{'通过':>7}{'通过率':>10}{'P0通过率':>11}")
    for lid in sorted(ev["layers"]):
        b = ev["layers"][lid]
        print(f"  {lid:<5}{b['name']:<14}{b['weight']:>5}%"
              f"{b['total']:>7}{b['passed']:>7}{b['pass_rate']:>9}%{b['p0_pass_rate']:>10}%")
    print("  " + "-" * 70)
    print()
    print("  发布门禁")
    print("  " + "-" * 70)
    for g in ev["gates"]:
        mark = "PASS" if g["passed"] else "FAIL"
        print(f"  [{mark}] {g['id']}  {g['rule']}")
        print(f"          实测 {g['actual']}（阈值 {g['threshold']}）")
        print(f"          {g['detail']}")
    print()
    print(f"  报告文件：{RESULTS / info['html']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())