"""架构约束检查器 —— 把「三条硬规则」从文档变成可执行的门禁。

为什么要这个脚本
----------------
``docs/DEVELOPMENT.md`` 里写了很多依赖约束，但只要没有可执行版本，它们就
只是约定：新增违规没人拦，检查器自己也不会被人盯着。本脚本零第三方依赖，
可直接挂进 CI。

规则
----
``R1-domain-purity``
    ``domains/<X>/domain/**`` 不得依赖 ``application`` / ``infrastructure``
    （含本域）。领域层只应有纯业务概念与端口声明。

``R2-cross-domain-infrastructure``
    不得跨域直接依赖对方的 ``infrastructure``。``domains/A`` 要用
    ``domains/B`` 的能力，应走 B 暴露的 ``application`` 或端口，而不是伸手
    进对方的基础设施实现。

``R3-domain-no-interfaces``
    ``domains/**`` 不得依赖 ``interfaces/**``。依赖方向必须是
    interfaces -> domains，反过来会形成环。

``R4-lazy-export-shadows-submodule``
    包的惰性导出名不得与本包子模块同名。子模块一旦被导入，CPython 会把它绑
    到父包的同名属性上，而 ``__getattr__`` 只在属性查找**失败**时才触发 ——
    惰性导出被永久遮蔽，进程启动即崩（``TypeError: 'module' object is not
    callable``）。见 ``docs/DEVELOPMENT.md`` 遗留 #13。

用法::

    python tools/check_architecture.py               # 违规 -> 退出码 1
    python tools/check_architecture.py --json        # CI 消费
    python tools/check_architecture.py --baseline    # 只拦新增违规
    python tools/check_architecture.py --update-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
from typing import Iterator, NamedTuple

#: 被扫描的顶层包。``tools`` 自身与 ``test`` 不参与规则判定。
SCAN_ROOTS = ("domains", "interfaces", "shared")

BASELINE_PATH = pathlib.Path(__file__).resolve().parent / ".architecture_baseline.json"

#: 领域层允许直接 import 的第三方/标准库顶层名。
#: 建模与类型工具是领域层的正当依赖，不属于「基础设施 SDK」。
ALLOWED_TOP_LEVEL = frozenset(
    {
        "abc", "asyncio", "collections", "contextlib", "dataclasses", "datetime",
        "decimal", "enum", "functools", "inspect", "itertools", "json", "logging",
        "math", "os", "pathlib", "re", "sys", "threading", "time", "typing",
        "uuid", "warnings", "weakref",
        "pydantic", "msgspec", "attrs",
    }
)


class Violation(NamedTuple):
    rule: str
    file: str
    line: int
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "file": self.file,
            "line": self.line,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# 惰性导出与子模块名（供 test_runtime_landmines 复用，避免两份判定逻辑漂移）
# ---------------------------------------------------------------------------


def _collect_lazy_map(tree: ast.AST) -> Iterator[tuple[str, str, int]]:
    """从 ``__init__.py`` 的 AST 里抽出惰性导出映射。

    yield ``(导出名, 目标模块, 行号)``。

    不硬编码 ``_LAZY`` 这个变量名：各包的惰性映射表名字并不统一，统一按
    「键是标识符字面量、值是形如 ``domains.x.y`` 的模块路径」这一特征识别。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            target = value.value
            # 只认项目内的模块路径；``__all__`` 之类的纯字符串列表会被排除
            if target.split(".")[0] not in SCAN_ROOTS:
                continue
            yield key.value, target, getattr(key, "lineno", getattr(node, "lineno", 0))


def _submodule_names(package_dir: pathlib.Path) -> set[str]:
    """返回该包目录下所有子模块/子包的名称（不含 ``__init__`` 自身）。"""
    names: set[str] = set()
    if not package_dir.is_dir():
        return names
    for entry in package_dir.iterdir():
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if entry.is_dir():
            if (entry / "__init__.py").exists():
                names.add(entry.name)
        elif entry.suffix == ".py" and entry.name != "__init__.py":
            names.add(entry.stem)
    return names


# ---------------------------------------------------------------------------
# 规则实现
# ---------------------------------------------------------------------------


def _parts_of(relative: pathlib.Path) -> list[str]:
    return [p for p in relative.parts]


def _in_domain_layer(parts: list[str]) -> bool:
    """是否位于 ``domains/<X>/domain/`` 之下。"""
    return len(parts) >= 3 and parts[0] == "domains" and parts[2] == "domain"


def _owner_domain(parts: list[str]) -> str | None:
    if parts and parts[0] == "domains" and len(parts) >= 2:
        return parts[1]
    return None


def _imported_module(node: ast.Import | ast.ImportFrom) -> str | None:
    if isinstance(node, ast.ImportFrom):
        # ``from . import x`` 是相对导入，无法静态判定目标包，跳过
        if node.level:
            return None
        return node.module
    return None


def _collect_imports(tree: ast.AST) -> Iterator[tuple[str, int, str]]:
    """yield ``(顶层包名, 行号, 完整导入路径)``。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = _imported_module(node)
            if module:
                yield module.split(".")[0], node.lineno, module


def _check_imports(
    path: pathlib.Path, root: pathlib.Path, tree: ast.AST
) -> Iterator[Violation]:
    rel = path.relative_to(root)
    parts = _parts_of(rel)
    owner = _owner_domain(parts)
    is_domain_layer = _in_domain_layer(parts)
    where = rel.as_posix()

    for top, lineno, module in _collect_imports(tree):
        mod_parts = module.split(".")

        # R3：domains 不依赖 interfaces
        if parts and parts[0] == "domains" and top == "interfaces":
            yield Violation(
                "R3-domain-no-interfaces",
                where,
                lineno,
                f"域层反向依赖展示层：import {module}",
            )
            continue

        if len(mod_parts) >= 2 and mod_parts[0] == "domains":
            target_domain = mod_parts[1]
            layer = mod_parts[2] if len(mod_parts) >= 3 else ""

            # R1 / R2 都只约束**领域层**：应用层编排时引用对方的 adapter /
            # store 是常见的组合根职责，一刀切会把正常的装配代码全判成违规。
            if is_domain_layer and layer == "infrastructure":
                if target_domain != owner:
                    yield Violation(
                        "R2-cross-domain-infrastructure",
                        where,
                        lineno,
                        f"领域层跨域直接依赖对方基础设施：{module}",
                    )
                else:
                    yield Violation(
                        "R1-domain-purity",
                        where,
                        lineno,
                        f"领域层依赖本域基础设施：{module}",
                    )
                continue


def _check_lazy_shadow(path: pathlib.Path, root: pathlib.Path) -> Iterator[Violation]:
    if path.name != "__init__.py":
        return
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return
    subs = _submodule_names(path.parent)
    if not subs:
        return
    where = path.relative_to(root).as_posix()
    for name, _target, lineno in _collect_lazy_map(tree):
        if name in subs:
            yield Violation(
                "R4-lazy-export-shadows-submodule",
                where,
                lineno,
                f"惰性导出名 {name!r} 与本包子模块同名，子模块导入后会遮蔽该导出",
            )


def scan(root: pathlib.Path) -> list[Violation]:
    violations: list[Violation] = []
    for top in SCAN_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            violations.extend(_check_imports(path, root, tree))
            violations.extend(_check_lazy_shadow(path, root))
    return violations


# ---------------------------------------------------------------------------
# 基线
# ---------------------------------------------------------------------------


def _key(v: Violation) -> str:
    return f"{v.rule}|{v.file}|{v.message}"


def _load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data.get("violations", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="架构约束检查器")
    parser.add_argument("--json", action="store_true", help="输出 JSON 供 CI 消费")
    parser.add_argument("--baseline", action="store_true", help="只报告基线之外的新增违规")
    parser.add_argument("--update-baseline", action="store_true", help="把当前违规写入基线")
    parser.add_argument("--root", default=None, help="项目根目录（默认本文件的上级）")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve() if args.root else pathlib.Path(__file__).resolve().parent.parent
    violations = scan(root)

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(
                {"violations": sorted(_key(v) for v in violations)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"基线已更新：{len(violations)} 条 -> {BASELINE_PATH}")
        return 0

    if args.baseline:
        known = _load_baseline()
        violations = [v for v in violations if _key(v) not in known]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not violations,
                    "count": len(violations),
                    "violations": [v.as_dict() for v in violations],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif violations:
        print(f"架构检查未通过：{len(violations)} 条违规")
        for v in violations:
            print(f"  [{v.rule}] {v.file}:{v.line} {v.message}")
    else:
        print("架构检查通过：0 违规")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
