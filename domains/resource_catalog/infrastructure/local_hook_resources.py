from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HookPolicy:
    """Hook 执行策略的默认值。"""

    continue_on_error: bool = True
    default_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class HookResources:
    """`LocalHookResourceLoader.load()` 的产物：分层合并后的 hook 配置快照。"""

    snapshot: dict[str, Any] = field(default_factory=dict)
    policy: HookPolicy = field(default_factory=HookPolicy)
    configuration_paths: tuple[Path, ...] = ()
    fingerprint: str = ""
    diagnostics: list[str] = field(default_factory=list)


def _normalize_directory(value: str | Path | None) -> Path | None:
    """把 str / Path 归一为绝对目录；未配置、空字符串统一返回 None。

    参数允许为 str 是因为 `context/runtime_builder.py` 的 `project_home`
    来自命令行，是字符串；也允许为 Path（来自 `get_config_dir()` 等）。
    目录不存在时 `resolve(strict=False)` 不会抛错，保证启动阶段可降级。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text).expanduser().resolve(strict=False)

#todo  本类自动生成，待补全
class LocalHookResourceLoader:
    """从 system / user / project 三层目录加载本地 hook 配置。

    构造函数只负责解析与保存搜索路径；真正的合并与解析在 `load()` 中进行，
    这样在配置缺失或解析失败时能安全降级为空快照，不阻断运行时启动。
    """

    _CONFIG_NAMES = ("hooks.json",)
    _PROJECT_DIR_NAME = ".rabbit"

    def __init__(
        self,
        *,
        project_home: str | Path | None = None,
        user_data_home: str | Path | None = None,
        system_config_dir: str | Path | None = None,
    ) -> None:
        self.project_home = _normalize_directory(project_home)
        self.user_data_home = _normalize_directory(user_data_home)
        self.system_config_dir = _normalize_directory(system_config_dir)

        # 搜索根目录按优先级从低到高固化：system → user → project。
        # 后续 project 层覆盖 user 层、user 层覆盖 system 层，即越靠近项目优先级越高。
        self._search_roots: tuple[Path, ...] = tuple(
            root
            for root in (
                self.system_config_dir,
                self.user_data_home,
                (self.project_home / self._PROJECT_DIR_NAME)
                if self.project_home is not None
                else None,
            )
            if root is not None
        )

    def load(self) -> HookResources:
        """扫描候选配置文件并合并为快照；无文件时返回空快照。"""
        diagnostics: list[str] = []
        used_paths: list[Path] = []
        merged: dict[str, Any] = {}
        hasher = hashlib.sha1()

        for path in self._candidate_paths():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            except json.JSONDecodeError as exc:
                diagnostics.append(f"invalid JSON in {path}: {exc}")
                continue
            if not isinstance(payload, dict):
                diagnostics.append(f"non-object payload in {path}, ignored")
                continue

            used_paths.append(path)
            for key, value in payload.items():
                if isinstance(value, dict):
                    merged.setdefault(key, {}).update(value)

            try:
                stat = path.stat()
                hasher.update(str(path).encode("utf-8"))
                hasher.update(str(int(stat.st_mtime)).encode("utf-8"))
            except OSError:
                pass

        return HookResources(
            snapshot=merged,
            policy=HookPolicy(),
            configuration_paths=tuple(used_paths),
            fingerprint=hasher.hexdigest() if used_paths else "",
            diagnostics=diagnostics,
        )

    def _candidate_paths(self) -> list[Path]:
        """按 system → user → project 的优先级顺序，返回实际存在的配置文件。"""
        return [
            root / name
            for root in self._search_roots
            for name in self._CONFIG_NAMES
            if (root / name).is_file()
        ]
