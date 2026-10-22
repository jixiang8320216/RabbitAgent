"""把架构约束检查本身也钉在测试里。

以前三条依赖规则只写在文档里，坏了没人知道；现在 ``tools/check_architecture.py``
能在 CI 里拦住新增违规，这条用例则保证**检查器自己不会被改坏**——否则它会
变成又一个「看起来有、实际不生效」的东西。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_architecture.py"


def test_guard_reports_clean_tree():
    """当前代码库应当零违规（存量已在本轮清零）。"""
    outcome = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert outcome.returncode == 0, (
        f"架构检查未通过:\n{outcome.stdout}\n{outcome.stderr}"
    )


def test_guard_detects_a_planted_violation(tmp_path):
    """插桩验证：真的写了条违规，检查器必须报出来（不是永真的空壳）。"""
    import json
    import shutil

    sandbox = tmp_path / "project"
    (sandbox / "domains" / "demo" / "domain").mkdir(parents=True)
    (sandbox / "tools").mkdir()
    shutil.copy(SCRIPT, sandbox / "tools" / "check_architecture.py")

    bad = sandbox / "domains" / "demo" / "domain" / "leaky.py"
    bad.write_text(
        "import domains.other.infrastructure.store\n",
        encoding="utf-8",
    )

    outcome = subprocess.run(
        [sys.executable, str(sandbox / "tools" / "check_architecture.py"), "--json"],
        cwd=str(sandbox), capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert outcome.returncode == 1
    payload = json.loads(outcome.stdout)
    assert any(v["rule"] == "R2-cross-domain-infrastructure" for v in payload["violations"])
