from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class SkillScope(StrEnum):
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True)
class Skill:
    """单个技能：来自某层目录的 SKILL.md。"""

    name: str
    description: str = ""
    scope: SkillScope = SkillScope.USER
    path: Path | None = None
    content: str = ""


@dataclass(frozen=True)
class SkillRegistry:
    """`discover_all()` 的产物：按名称去重后的技能集合。"""

    _skills: tuple[Skill, ...] = ()

    def list_all(self) -> tuple[Skill, ...]:
        return self._skills

    def get(self, name: str) -> Skill | None:
        target = name.strip()
        for skill in self._skills:
            if skill.name == target:
                return skill
        return None

    def __len__(self) -> int:
        return len(self._skills)

#todo  本类自动生成，待补全
class LocalSkillDiscoveryService:
    """从 system / user / project 三层目录发现 SKILL.md 技能。

    构造函数只负责解析与保存搜索路径；`discover_all()` 执行真正的扫描与合并，
    在文件缺失或解析失败时安全降级为空注册表，不阻断运行时启动。
    """

    _SKILL_FILE_NAME = "SKILL.md"
    _FRONTMATTER_RE = re.compile(
        r"^\s*---\s*\n(?P<body>.*?)\n\s*---\s*\n?(?P<rest>.*)$",
        re.DOTALL,
    )

    def __init__(
        self,
        *,
        project_home: str | Path | None,
        user_data_home: str | Path | None,
        system_config_dir: str | Path | None,
    ) -> None:
        self.project_home = (
            Path(project_home).resolve(strict=False) if project_home else None
        )
        self.user_data_home = (
            Path(user_data_home).resolve(strict=False) if user_data_home else None
        )
        self.system_config_dir = (
            Path(system_config_dir).resolve(strict=False)
            if system_config_dir
            else None
        )

    def discover_all(self) -> SkillRegistry:
        """扫描三层目录下的 SKILL.md，按名称合并（project 覆盖 user 覆盖 system）。"""
        merged: dict[str, Skill] = {}
        for path, scope in self._candidate_files():
            skill = self._parse_skill(path, scope)
            if skill is not None:
                merged[skill.name] = skill  # 后者覆盖前者
        skills = sorted(merged.values(), key=lambda skill: skill.name)
        return SkillRegistry(tuple(skills))

    def load_rabbit_skills_md(self) -> str:
        """把已发现的技能渲染成聚合 markdown，供 rabbit 注入；无技能时返回空串。"""
        skills = self.discover_all().list_all()
        if not skills:
            return ""
        sections: list[str] = []
        for skill in skills:
            header = f"# {skill.name}"
            if skill.description:
                header += f"\n\n{skill.description}"
            sections.append(f"{header}\n\n{skill.content.strip()}".rstrip())
        return "\n\n---\n\n".join(sections)

    def _candidate_files(self) -> list[tuple[Path, SkillScope]]:
        """system → user → project 的优先级顺序，递归返回存在的 SKILL.md。"""
        roots: list[tuple[Path, SkillScope]] = []
        if self.system_config_dir is not None:
            roots.append((self.system_config_dir, SkillScope.SYSTEM))
        if self.user_data_home is not None:
            roots.append((self.user_data_home, SkillScope.USER))
        if self.project_home is not None:
            roots.append((self.project_home / ".rabbit", SkillScope.PROJECT))

        result: list[tuple[Path, SkillScope]] = []
        for root, scope in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob(self._SKILL_FILE_NAME)):
                if path.is_file():
                    result.append((path, scope))
        return result

    @classmethod
    def _parse_skill(cls, path: Path, scope: SkillScope) -> Skill | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

        frontmatter, body = cls._split_frontmatter(raw)
        name = (frontmatter.get("name") or path.parent.name).strip()
        if not name:
            return None
        description = (frontmatter.get("description") or "").strip()
        return Skill(
            name=name,
            description=description,
            scope=scope,
            path=path,
            content=body.strip(),
        )

    @classmethod
    def _split_frontmatter(cls, text: str) -> tuple[dict[str, str], str]:
        match = cls._FRONTMATTER_RE.match(text)
        if match is None:
            return {}, text
        values: dict[str, str] = {}
        for line in match.group("body").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip().strip("\"'")
        return values, match.group("rest")
