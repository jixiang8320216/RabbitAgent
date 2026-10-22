"""锁住「提示词文件加载」与「技能提示词上下文」两份实现的契约。

这两个模块此前都是 **0 字节空文件**，但调用方已经写好并且在跑
（``SystemPromptBuilder`` 里 ``PromptFileLoader`` 有 2 处构造、``_parse_frontmatter``
有 1 处、``build_skill_summary_context`` 有 1 处）。也就是说调用契约先于实现存在，
测试的首要任务是**把既有契约钉死**，避免后续改动悄悄改变行为：

- 相对路径的查找顺序必须是 ``system_config_dir`` 优先于 ``project_home``；
- ``load`` 返回的是**去掉 front-matter 的正文**，不是原文；
- 任何失败都返回空串而不是抛异常（提示词是装配素材，只能降级）。

其中「降级不抛」这一条最容易被破坏：后来者看到 ``except`` 会觉得「吞异常不好」
而改成抛出，结果一个拼错的提示词文件名就会让整个 agent 装配失败。
"""

from __future__ import annotations

import pathlib

import domains.agent.application.prompt.prompt_file_loader as prompt_file_loader
import domains.agent.application.prompt.skill_prompt_context as skill_prompt_context


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

class _FakeSkillPackage:
    """``SkillPackage`` 的替身：只提供本模块实际读取的字段。"""

    def __init__(self, name: str, description: str, path: pathlib.Path) -> None:
        self.name = name
        self.description = description
        self.path = path


class _FakeRegistry:
    """``SkillRegistry`` 的替身：只需 ``get`` / ``load`` / ``__len__``。

    刻意**不**导入真实的 ``SkillRegistry``：这里验证的是「上下文渲染」，
    用真实注册表就需要先造出磁盘上的技能包目录，把测试重心带偏。
    """

    def __init__(self, packages: dict[str, _FakeSkillPackage]) -> None:
        self._packages = packages
        self.load_calls = 0

    def get(self, name: str):
        return self._packages.get(name)

    def load(self):
        self.load_calls += 1
        return self

    def __len__(self) -> int:
        return len(self._packages)


class _FakeAgent:
    """``AgentRuntime`` 的替身：只提供技能相关字段。"""

    def __init__(
        self,
        *,
        skill_refs: list[str] | None = None,
        package_skill_names: list[str] | None = None,
        skill_name: str | None = None,
        skill_dir: pathlib.Path | None = None,
    ) -> None:
        self.skill_refs = skill_refs
        self.package_skill_names = package_skill_names or []
        self.skill_name = skill_name
        self.skill_dir = skill_dir


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

def test_parse_frontmatter_splits_metadata_and_body():
    metadata, body = prompt_file_loader._parse_frontmatter(
        "---\nname: planner\ndescription: 规划者\n---\n\n正文内容"
    )
    assert metadata == {"name": "planner", "description": "规划者"}
    assert "正文内容" in body
    assert "name: planner" not in body


def test_parse_frontmatter_without_frontmatter_returns_original():
    raw = "# 标题\n\n没有 front-matter"
    metadata, body = prompt_file_loader._parse_frontmatter(raw)
    assert metadata == {}
    assert body == raw


def test_parse_frontmatter_unterminated_raises():
    """有起始分隔符但没闭合 -> 必须抛 ``FrontmatterParseError``。

    调用方 ``SystemPromptBuilder`` 靠这个异常把「格式错误」与「文件读不到」
    区分开（前者 return ""，后者记 error 日志），所以异常类型不能变。
    """
    try:
        prompt_file_loader._parse_frontmatter("---\nname: x\n没有闭合")
    except prompt_file_loader.FrontmatterParseError:
        return
    raise AssertionError("未闭合的 front-matter 应抛 FrontmatterParseError")


def test_parse_frontmatter_strips_bom_and_quotes():
    metadata, _body = prompt_file_loader._parse_frontmatter(
        "\ufeff---\nname: \"quoted\"\nother: 'single'\n---\nbody"
    )
    assert metadata == {"name": "quoted", "other": "single"}


def test_parse_frontmatter_ignores_comment_and_non_kv_lines():
    metadata, body = prompt_file_loader._parse_frontmatter(
        "---\n# 注释行\nnot-a-kv-line\nname: ok\n---\nbody"
    )
    assert metadata == {"name": "ok"}
    assert body.strip() == "body"


# ---------------------------------------------------------------------------
# PromptFileLoader：路径解析
# ---------------------------------------------------------------------------

def test_load_resolves_absolute_path(tmp_path):
    target = tmp_path / "abs.md"
    target.write_text("绝对路提示词", encoding="utf-8")
    loader = prompt_file_loader.PromptFileLoader()
    assert loader.load(str(target)) == "绝对路提示词"


def test_relative_path_prefers_system_config_dir_over_project_home(tmp_path):
    """查找顺序：``system_config_dir`` 优先——系统级策略覆盖项目级。"""
    system_dir = tmp_path / "system"
    project_dir = tmp_path / "project"
    (system_dir / "prompts").mkdir(parents=True)
    (project_dir / "prompts").mkdir(parents=True)
    (system_dir / "prompts" / "p.md").write_text("系统级", encoding="utf-8")
    (project_dir / "prompts" / "p.md").write_text("项目级", encoding="utf-8")

    loader = prompt_file_loader.PromptFileLoader(
        project_home=project_dir, system_config_dir=system_dir,
    )
    assert loader.load("prompts/p.md") == "系统级"


def test_relative_path_falls_back_to_project_home(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "p.md").write_text("项目级", encoding="utf-8")
    loader = prompt_file_loader.PromptFileLoader(project_home=project_dir)
    assert loader.load("p.md") == "项目级"


# ---------------------------------------------------------------------------
# PromptFileLoader：降级不抛
# ---------------------------------------------------------------------------

def test_load_returns_empty_for_missing_file(tmp_path):
    loader = prompt_file_loader.PromptFileLoader(project_home=tmp_path)
    assert loader.load("不存在的.md") == ""


def test_load_returns_empty_for_empty_or_none_input():
    loader = prompt_file_loader.PromptFileLoader()
    assert loader.load("") == ""
    assert loader.load(None) == ""
    assert loader.load("   ") == ""


def test_load_returns_empty_when_frontmatter_unterminated(tmp_path):
    """``load`` 必须吞掉格式错误并降级——不能把 ``FrontmatterParseError`` 抛给装配链路。"""
    target = tmp_path / "bad.md"
    target.write_text("---\nname: x\n没有闭合", encoding="utf-8")
    loader = prompt_file_loader.PromptFileLoader()
    assert loader.load(str(target)) == ""


def test_load_returns_empty_when_file_too_large(tmp_path):
    target = tmp_path / "huge.md"
    target.write_text("x" * (prompt_file_loader.MAX_PROMPT_FILE_BYTES + 1),
                      encoding="utf-8")
    loader = prompt_file_loader.PromptFileLoader()
    assert loader.load(str(target)) == ""


def test_load_strips_frontmatter_from_prompt_file(tmp_path):
    """模式提示词文件也可能带 front-matter；正文里不该出现元数据行。"""
    target = tmp_path / "mode.md"
    target.write_text("---\nname: solo\n---\n\n模式正文", encoding="utf-8")
    loader = prompt_file_loader.PromptFileLoader()
    body = loader.load(str(target))
    assert body == "模式正文"
    assert "name: solo" not in body


def test_load_with_metadata_returns_both(tmp_path):
    target = tmp_path / "mode.md"
    target.write_text("---\nname: solo\n---\n\n正文", encoding="utf-8")
    loader = prompt_file_loader.PromptFileLoader()
    metadata, body = loader.load_with_metadata(str(target))
    assert metadata == {"name": "solo"}
    assert body == "正文"


# ---------------------------------------------------------------------------
# 技能提示词上下文
# ---------------------------------------------------------------------------

def test_summary_lists_skill_with_description():
    package = _FakeSkillPackage("data_analysis", "数据分析", pathlib.Path("/s/data/SKILL.md"))
    registry = _FakeRegistry({"data_analysis": package})
    agent = _FakeAgent(skill_refs=["data_analysis"])

    text = skill_prompt_context.build_skill_summary_context(agent, registry)
    assert "data_analysis" in text
    assert "数据分析" in text
    assert text.startswith("# AVAILABLE SKILLS")


def test_summary_without_registry_still_lists_names():
    """注册表缺席时退化成「只有名字」——声明本身也是信息。"""
    agent = _FakeAgent(skill_refs=["orphan"])
    text = skill_prompt_context.build_skill_summary_context(agent, None)
    assert "orphan" in text


def test_summary_marks_unregistered_skill():
    """声明了但注册表里没有 -> 显式标注，让配置漂移可见而不是静默消失。"""
    registry = _FakeRegistry({})
    agent = _FakeAgent(skill_refs=["ghost"])
    text = skill_prompt_context.build_skill_summary_context(agent, registry)
    assert "ghost" in text
    assert "未注册" in text


def test_summary_returns_empty_when_no_skill_declared():
    agent = _FakeAgent()
    assert skill_prompt_context.build_skill_summary_context(agent, None) == ""


def test_skill_names_deduplicated_and_order_preserved():
    """三个来源合并时去重，且保持声明顺序（先 skill_refs 后 package 再 skill_name）。"""
    registry = _FakeRegistry({})
    agent = _FakeAgent(
        skill_refs=["b", "a"],
        package_skill_names=["a", "c"],
        skill_name="b",
    )
    text = skill_prompt_context.build_skill_summary_context(agent, registry)
    positions = [text.index(name) for name in ("b", "a", "c")]
    assert positions == sorted(positions)
    assert text.count("b") == 1  # 去重：只出现一次


def test_runtime_context_includes_manifest_path_and_siblings(tmp_path):
    skill_dir = tmp_path / "data_analysis"
    skill_dir.mkdir()
    manifest = skill_dir / "SKILL.md"
    manifest.write_text("正文", encoding="utf-8")
    (skill_dir / "helper.py").write_text("# 脚本", encoding="utf-8")

    package = _FakeSkillPackage("data_analysis", "数据分析", manifest)
    registry = _FakeRegistry({"data_analysis": package})
    agent = _FakeAgent(skill_refs=["data_analysis"])

    text = skill_prompt_context.build_skill_runtime_context(agent, registry)
    assert "SKILL.md" in text
    assert "helper.py" in text  # 附带资源要列出来，供模型按需读取
    assert text.startswith("# SKILL RUNTIME CONTEXT")


def test_runtime_context_returns_empty_when_nothing_available():
    agent = _FakeAgent()
    assert skill_prompt_context.build_skill_runtime_context(agent, None) == ""


def test_ensure_loaded_triggers_registry_load_once(tmp_path):
    """注册表构造后不会自动扫描；渲染上下文时要兜一次，且只兜一次。"""
    manifest = tmp_path / "SKILL.md"
    manifest.write_text("正文", encoding="utf-8")
    package = _FakeSkillPackage("s", "描述", manifest)
    registry = _FakeRegistry({"s": package})
    agent = _FakeAgent(skill_refs=["s"])

    skill_prompt_context.build_skill_summary_context(agent, registry)
    skill_prompt_context.build_skill_summary_context(agent, registry)
    assert registry.load_calls == 0  # 非空注册表视为已加载，不重复扫盘
