from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstructionResource:
    """单条会话起始指令资源。"""

    path: Path
    scope: str
    content: str

#todo  本类自动生成，待补全
class LocalInstructionResourceLoader:
    """从 user / project 两层目录加载会话起始指令（INSTRUCTIONS.md / AGENTS.md）。

    构造函数只负责解析与保存搜索路径；`load_session_start()` 执行真正的读取，
    在文件缺失或读取失败时安全降级为空列表，不阻断运行时启动。
    """

    _INSTRUCTION_FILE_NAMES = ("INSTRUCTIONS.md", "AGENTS.md")

    def __init__(
        self,
        *,
        project_home: str | Path | None,
        user_data_home: str | Path | None,
    ) -> None:
        self.project_home = (
            Path(project_home).resolve(strict=False) if project_home else None
        )
        self.user_data_home = (
            Path(user_data_home).resolve(strict=False) if user_data_home else None
        )

    def load_session_start(self) -> list[InstructionResource]:
        """按 user → project 顺序加载指令文件；返回有序条目（project 靠后以便覆盖）。"""
        entries: list[InstructionResource] = []
        for path, scope in self._candidate_paths():
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            entries.append(InstructionResource(path=path, scope=scope, content=content))
        return entries

    def _candidate_paths(self) -> list[tuple[Path, str]]:
        """user → project 的优先级顺序，返回实际存在的指令文件。"""
        roots: list[tuple[Path, str]] = []
        if self.user_data_home is not None:
            roots.append((self.user_data_home, "user"))
        if self.project_home is not None:
            roots.append((self.project_home / ".rabbit", "project"))

        return [
            (root / name, scope)
            for root, scope in roots
            for name in self._INSTRUCTION_FILE_NAMES
            if (root / name).is_file()
        ]
