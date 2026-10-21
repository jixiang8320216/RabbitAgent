"""技能注册表。

此前本模块是**空文件**，而项目里有两处真实依赖它：

- ``agent/service/agent_factory.py`` ——
  ``skill_registry: typing.Optional[skill_registry_module.SkillRegistry]``
- ``agent/service/agent_toolkit_builder.py`` —— 同上，
  且注释明确写着「技能工具：``SkillRegistry`` 为空模块，同样降级」

也就是说：技能能力在架构上被预留了位置，但**实现缺失**，
凡是构造 ``AgentFactory`` / ``AgentToolkitBuilder`` 的路径都会在
类型注解求值或属性访问时踩空。

技能包约定
----------
一个技能是一个目录，内含 ``SKILL.md``，头部为 front-matter::

    ---
    name: data_analysis
    description: 数据分析与可视化
    ---

    # 正文（会作为技能指令注入系统提示词）

本模块负责：**发现 -> 解析 -> 索引 -> 路由 -> 按名取用**。

安全约束
--------
技能内容会被注入到 agent 的系统提示词里，因此加载时必须守住两条：

1. **路径不逃逸**：只在配置的搜索根目录内查找，拒绝 ``..`` 穿越；
2. **大小有上限**：单个技能文件与总数量设上限，避免异常包撑爆上下文。
"""

from __future__ import annotations

import logging
import pathlib
import typing

logger = logging.getLogger(__name__)

#: 单个技能文件的大小上限（字节）。技能指令会进系统提示词，
#: 过大既浪费上下文也可能带来提示词注入，故设硬上限。
MAX_SKILL_FILE_BYTES = 256 * 1024

#: 单次扫描允许加载的技能总数上限。
MAX_SKILL_COUNT = 500

#: 技能清单文件名（大小写两种都认）。
SKILL_MANIFEST_NAMES = ("SKILL.md", "skill.md")

#: 技能搜索根目录的默认候选（相对项目根）。
DEFAULT_SEARCH_DIRS = (
    "skills",
    ".claude/skills",
    ".workbuddy/skills",
)


class SkillPackage:
    """一个已解析的技能包。

    :param name: 技能名（front-matter 的 ``name``，缺省用目录名）。
    :param description: 一句话描述，用于路由与列表展示。
    :param path: ``SKILL.md`` 的路径。
    :param instructions: 正文内容（注入系统提示词的部分）。
    :param source_root: 该技能所属的搜索根目录。
    :param metadata: front-matter 里其余字段。
    """

    __slots__ = ("name", "description", "path", "instructions",
                 "source_root", "metadata")

    def __init__(
        self,
        name: str,
        description: str,
        path: pathlib.Path,
        instructions: str,
        source_root: pathlib.Path,
        metadata: typing.Mapping[str, typing.Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.path = path
        self.instructions = instructions
        self.source_root = source_root
        self.metadata = dict(metadata or {})

    def as_prompt_block(self) -> str:
        """渲染成注入系统提示词的片段。"""
        header = f"## 技能：{self.name}"
        if self.description:
            header += f" —— {self.description}"
        return f"{header}\n\n{self.instructions}".strip()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"SkillPackage(name={self.name!r}, path={str(self.path)!r})"


class SkillRegistry:
    """技能注册表：按名字索引已加载的技能包。

    :param search_roots: 技能搜索根目录列表；为 ``None`` 时用
        :data:`DEFAULT_SEARCH_DIRS` 相对当前工作目录解析。
    """

    def __init__(
        self,
        search_roots: typing.Sequence[str | pathlib.Path] | None = None,
    ) -> None:
        if search_roots is None:
            roots = [pathlib.Path.cwd() / d for d in DEFAULT_SEARCH_DIRS]
        else:
            roots = [pathlib.Path(r) for r in search_roots]
        self.search_roots: tuple[pathlib.Path, ...] = tuple(roots)
        self._skills: dict[str, SkillPackage] = {}
        #: 加载过程中的告警（缺目录、解析失败、超限等），供诊断使用。
        self.load_warnings: list[str] = []

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def load(self, *, force: bool = False) -> "SkillRegistry":
        """扫描所有搜索根目录并加载技能。

        :param force: 为 ``True`` 时即使已有缓存也重新扫描。
        :return: ``self``，便于链式调用。
        """
        if self._skills and not force:
            return self

        self._skills.clear()
        self.load_warnings.clear()

        for root in self.search_roots:
            resolved = self._resolve_root(root)
            if resolved is None or not resolved.is_dir():
                continue
            try:
                children = sorted(p for p in resolved.iterdir() if p.is_dir())
            except OSError as error:
                self.load_warnings.append(f"扫描目录失败 {resolved}: {error}")
                continue
            for child in children:
                if len(self._skills) >= MAX_SKILL_COUNT:
                    self.load_warnings.append(
                        f"技能数达到上限 {MAX_SKILL_COUNT}，跳过 {child.name}"
                    )
                    break
                package = self._load_one(child, resolved)
                if package is None:
                    continue
                # 同名技能：后加载的覆盖先加载的（与命令注册表语义一致），
                # 但记录告警，避免静默覆盖掩盖配置问题。
                if package.name in self._skills:
                    self.load_warnings.append(
                        f"技能名重复：{package.name}"
                        f"（{self._skills[package.name].path} -> "
                        f"{package.path}），后者覆盖前者"
                    )
                self._skills[package.name] = package

        if not self._skills:
            logger.debug("未发现任何技能包，搜索根目录=%s",
                         [str(r) for r in self.search_roots])
        return self

    @staticmethod
    def _resolve_root(root: pathlib.Path) -> pathlib.Path | None:
        """解析并校验搜索根目录，拒绝无效路径。"""
        try:
            resolved = root.resolve()
        except OSError:
            return None
        return resolved if resolved.is_absolute() else None

    def _load_one(
        self, package_dir: pathlib.Path, root: pathlib.Path
    ) -> SkillPackage | None:
        """加载单个技能目录，失败返回 ``None``（并记录告警）。"""
        manifest: pathlib.Path | None = None
        for candidate_name in SKILL_MANIFEST_NAMES:
            candidate = package_dir / candidate_name
            if candidate.is_file():
                manifest = candidate
                break
        if manifest is None:
            return None

        # 路径不逃逸：技能目录必须在搜索根之内
        try:
            package_dir.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            self.load_warnings.append(
                f"技能目录逃逸出搜索根，已拒绝：{package_dir}"
            )
            return None

        try:
            size = manifest.stat().st_size
        except OSError as error:
            self.load_warnings.append(f"读取技能清单失败 {manifest}: {error}")
            return None
        if size > MAX_SKILL_FILE_BYTES:
            self.load_warnings.append(
                f"技能清单过大已跳过：{manifest}（{size} > "
                f"{MAX_SKILL_FILE_BYTES} 字节）"
            )
            return None

        try:
            raw = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            self.load_warnings.append(f"读取技能内容失败 {manifest}: {error}")
            return None

        meta, body = _split_front_matter(raw)
        name = str(meta.get("name") or package_dir.name).strip()
        description = str(meta.get("description") or "").strip()
        if not name:
            self.load_warnings.append(f"技能名为空，已跳过：{manifest}")
            return None

        return SkillPackage(
            name=name,
            description=description,
            path=manifest,
            instructions=body.strip(),
            source_root=root,
            metadata=meta,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, name: str) -> SkillPackage | None:
        """按名字取技能。"""
        return self._skills.get(name)

    def list_skills(self) -> list[SkillPackage]:
        """列出全部已加载技能（按名字排序）。"""
        return [self._skills[k] for k in sorted(self._skills)]

    def names(self) -> list[str]:
        """列出全部技能名。"""
        return sorted(self._skills)

    def exists(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __iter__(self) -> typing.Iterator[SkillPackage]:
        return iter(self.list_skills())

    # ------------------------------------------------------------------
    # 路由（验收 L1-2.1「加载正确性/路由命中率」）
    # ------------------------------------------------------------------
    def route(
        self, task_description: str, top_k: int = 1
    ) -> list[SkillPackage]:
        """按任务描述挑选最匹配的技能。

        采用**关键词重叠**打分而非向量检索——刻意保持零外部依赖，
        使技能路由在没有 embedding 服务时也可用（可解释、可测试）。

        :param task_description: 用户任务描述。
        :param top_k: 返回数量。
        :return: 得分最高的 ``top_k`` 个技能；**无任何匹配时返回空列表**——
            不硬塞一个不相关的技能，这正是 L1-2.1 要防的误路由。
        """
        text = (task_description or "").lower()
        if not text.strip():
            return []
        scored: list[tuple[int, str, SkillPackage]] = []
        for package in self.list_skills():
            score = _match_score(text, package)
            if score > 0:
                scored.append((score, package.name, package))
        if not scored:
            return []
        # 分数降序；同分按名字升序，保证结果稳定可复现
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [pkg for _, _, pkg in scored[:top_k]]

    def build_prompt_block(self, names: typing.Sequence[str]) -> str:
        """把指定技能渲染成系统提示词片段。"""
        blocks: list[str] = []
        for name in names:
            package = self.get(name)
            if package is not None:
                blocks.append(package.as_prompt_block())
        return "\n\n".join(blocks)

    def describe(self) -> str:
        """人类可读的状态描述（供 ``/skills`` 命令展示）。"""
        if not self._skills:
            roots = ", ".join(str(r) for r in self.search_roots)
            return (f"未发现任何技能包。\n"
                    f"已搜索：{roots}\n"
                    f"技能包需为 <根目录>/<技能名>/SKILL.md，"
                    f"头部含 YAML front-matter（name / description）。")
        lines = [f"已加载技能（{len(self._skills)} 个）："]
        for package in self.list_skills():
            desc = f" —— {package.description}" if package.description else ""
            lines.append(f"  - {package.name}{desc}")
        if self.load_warnings:
            lines.append("")
            lines.append(f"加载告警（{len(self.load_warnings)} 条）：")
            lines.extend(f"  ! {w}" for w in self.load_warnings)
        return "\n".join(lines)


class SkillRegistryRefresher:
    """技能注册表刷新器。

    存在的意义：技能包是**磁盘上的文件**，运行期可能被新增/修改/删除。
    提供统一刷新入口，让调用方不必关心注册表内部的缓存策略。

    :param registry: 被刷新的注册表；为 ``None`` 时内部自建一个。
    :param search_roots: 仅在自建注册表时使用。
    """

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        search_roots: typing.Sequence[str | pathlib.Path] | None = None,
    ) -> None:
        self.registry = (registry if registry is not None
                         else SkillRegistry(search_roots))
        self.refresh_count = 0

    def refresh(self) -> SkillRegistry:
        """强制重新扫描并返回注册表。"""
        self.registry.load(force=True)
        self.refresh_count += 1
        return self.registry

    def ensure_loaded(self) -> SkillRegistry:
        """确保已加载（已加载则直接返回，不重复扫描）。"""
        return self.registry.load()

    def list_skills(self) -> list[SkillPackage]:
        self.ensure_loaded()
        return self.registry.list_skills()

    def route(
        self, task_description: str, top_k: int = 1
    ) -> list[SkillPackage]:
        self.ensure_loaded()
        return self.registry.route(task_description, top_k=top_k)


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
def _split_front_matter(
    raw: str,
) -> tuple[dict[str, typing.Any], str]:
    """拆出 front-matter 与正文。

    只支持 front-matter 最基本的 ``key: value`` 形式（不引入 PyYAML 依赖），
    这足以覆盖 skill 包的必需字段（name / description）。
    """
    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines:
        return {}, text
    end = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end < 0:
        return {}, text

    meta: dict[str, typing.Any] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            meta[key] = value
    return meta, "\n".join(lines[end + 1:])


def _match_score(task_text: str, package: SkillPackage) -> int:
    """关键词重叠打分。"""
    haystack = f"{package.name} {package.description}".lower()
    if not haystack.strip():
        return 0
    score = 0
    # 技能名整体命中权重最高
    if package.name.lower() in task_text:
        score += 5
    for token in _tokenize(haystack):
        if token in task_text:
            score += 1
    return score


def _tokenize(text: str) -> set[str]:
    """粗粒度分词：英文单词 + 中文 2-gram。"""
    tokens: set[str] = set()
    buffer: list[str] = []
    for char in text:
        if char.isascii() and (char.isalnum() or char in "_-"):
            buffer.append(char)
        else:
            if buffer:
                tokens.add("".join(buffer).lower())
                buffer.clear()
            if not char.isspace():
                tokens.add(char)
    if buffer:
        tokens.add("".join(buffer).lower())

    cjk = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    return {t for t in tokens if len(t) >= 2}


__all__ = [
    "SkillPackage",
    "SkillRegistry",
    "SkillRegistryRefresher",
    "MAX_SKILL_FILE_BYTES",
    "MAX_SKILL_COUNT",
    "DEFAULT_SEARCH_DIRS",
]