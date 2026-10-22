# -*- coding: utf-8 -*-
"""一键执行完整验证套件。

用法
----
    python test/run_verification.py                 # 全部阶段
    python test/run_verification.py --stage1        # 只跑第一阶段（阻断性风险）
    python test/run_verification.py --smoke         # 冒烟：L0 + L1 核心 + L4 P0
    python test/run_verification.py --release# 发布前全量门禁（P0 任一失败即退出码 2）

退出码
------
    0  门禁通过
    1  测试执行异常
    2  门禁未通过（存在 P0 失败或总分不足）
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import subprocess
import sys

TEST_ROOT = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = TEST_ROOT.parent

STAGES = {
    "stage1": ["stage1_security"],
    "stage2": ["stage2_capability"],
    "stage3": ["stage3_cost"],
    "stage4": ["stage4_robustness"],
}

# 指南 4.3：冒烟阶段覆盖 L0 + L1 核心 + L4 P0
SMOKE_TARGETS = [
    "stage1_security",
    "stage2_capability/test_l0_connectivity.py",
    "stage2_capability/test_l1_3_hook.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="RabbitAgent 智能体验证执行器")
    parser.add_argument(
        "--stage", choices=sorted(STAGES), help="只执行指定阶段"
    )
    parser.add_argument("--smoke", action="store_true", help="执行冒烟集")
    parser.add_argument(
        "--release", action="store_true", help="发布门禁模式：P0 失败即以退出码 2 结束"
    )
    parser.add_argument("--no-baseline", action="store_true", help="不与上次结果做退化对比")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示每条用例")
    args = parser.parse_args()

    if args.smoke:
        targets = [str(TEST_ROOT / t) for t in SMOKE_TARGETS]
        label = "冒烟集（L0 + L1 核心 + L4 P0）"
    elif args.stage:
        targets = [str(TEST_ROOT / t) for t in STAGES[args.stage]]
        label = f"{args.stage}"
    else:
        targets = [str(TEST_ROOT / f"stage{i}_*") for i in range(1, 5)]
        label = "全量（L0–L5）"

    # subprocess 不经过 shell，且 Path.glob 不支持绝对路径，需用 glob.glob 展开
    expanded: list[str] = []
    for target in targets:
        if any(ch in target for ch in "*?"):
            matches = sorted(glob.glob(target))
            if not matches:
                print(f"警告：{target} 未匹配到任何目录")
                continue
            expanded.extend(matches)
        else:
            expanded.append(target)

    cmd = [
        sys.executable, "-m", "pytest",
        *expanded,
        "-q", "--no-header",
    ]
    if args.verbose:
        cmd.append("-v")
    else:
        cmd.append("--tb=line")
    if args.no_baseline:
        cmd.append("--no-baseline")

    print("=" * 68)
    print(f"  RabbitAgent 智能体验证 —— {label}")
    print(f"  Python: {sys.executable}")
    print(f"  用例目录: {TEST_ROOT}")
    print("=" * 68 + "\n")

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)

    if args.release and result.returncode != 0:
        latest = TEST_ROOT / "results" / "latest.json"
        if latest.exists():
            info = json.loads(latest.read_text(encoding="utf-8"))
            if not info.get("gate_passed", False):
                print("\n发布门禁未通过 — 禁止发布。详见 test/results/ 下的报告。")
                return 2
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())