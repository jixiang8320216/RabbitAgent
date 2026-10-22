"""提示词文件加载器：把磁盘上的提示词文件读成可直接注入系统提示词的正文。

为什么要单独一个模块
--------------------
``SystemPromptBuilder`` 里有三处「读提示词文件」的需求，语义完全一致：

1. 模式根 / 插件根的模式提示词文件（``_build_for_plan_like``）；
2. 子 agent 槽位声明的 ``system_prompt_file``（``_extract_sub_agent_base_prompt``）；
3. 技能包 ``SKILL.md`` 的正文抽取（同上，走 :func:`_parse_frontmatter`）。

此前这三处各自内联实现：路径解析规则散落在不同方法里，front-matter 解析则与
``skill_registry`` / ``local_skill_discovery`` 各写一份。本模块把它们收敛到一处，
保证**同一个文件名在任何入口下都解析到同一个文件**，也让「提示词文件怎么找」
这件事有唯一一处可以改。

路径解析规则
------------
::

    绝对路径            -> 直用（调用方明确指定了位置）
    相对路径            -> 依次试 system_config_dir/<p> -> project_home/<p>
    都不存在 / 读不到    -> 空串（降级，不抛）

这个「system_config_dir 优先于 project_home」的顺序与 ``root_agent_loader`` 的
既有约定一致：**系统级配置覆盖项目级**，因此项目里放一份同名提示词文件不会
意外顶掉系统级策略。

设计原则
--------
- **绝不抛异常**：提示词是 agent 的装配素材，读不到只能降级为空串，不能让
  agent 装配整体失败。唯一的例外是 :class:`FrontmatterParseError`——它由
  :func:`_parse_frontmatter` 抛出、交给调用方决定，这是既有调用契约
  （``SystemPromptBuilder._extract_sub_agent_base_prompt`` 依赖它区分
  「格式错误」与「IO 错误」两种失败）。
- **零第三方依赖**：只用标准库，不引入 PyYAML。front-matter 只需要
  ``key: value`` 这一种形式就够覆盖提示词文件的全部必需字段。理由与
  ``skill_registry`` 一致——保证依赖未装齐的环境下也能跑。
- **大小有上限**：提示词文件会被整份注入上下文，异常大的文件既浪费窗口、
  也可能带来提示词注入，故设硬上限。

安全说明
--------
本模块**不**对绝对路径做逃逸限制：提示词文件是本机可信配置，调用方既然给了
绝对路径就是明确的意图，拦截只会让正常用法失效。相对路径则受
``system_config_dir`` / ``project_home`` 两个基目录约束，天然无法越过。
"""

from __future__ import annotations

import logging
import pathlib
import typing

logger = logging.getLogger(__name__)

#: 单个提示词文件的大小上限（字节）。
#:
#: 提示词文件会被整份读进上下文，1 MiB 对纯文本提示词已是极端值；超限时
#: 视为异常文件直接跳过，而不是截断——截断会产出半截的指令，比不注入更危险。
MAX_PROMPT_FILE_BYTES = 1024 * 1024

#: front-matter 分隔符。
_FRONTMATTER_DELIMITER = "---"


class FrontmatterParseError(ValueError):
    """front-matter 格式错误：有起始分隔符但没有闭合分隔符。

    继承 ``ValueError`` 而非 ``Exception``，让「内容格式有问题」与「文件读不到」
    在调用方眼里是两类不同的失败——前者值得告警（文件写错了），后者通常是
    配置里根本没配，属于常态。
    """


def _parse_frontmatter(raw_content: str) -> tuple[dict[str, str], str]:
    """拆出 front-matter 与正文。

    :param raw_content: 文件原始内容。
    :return: ``(metadata, body)``；无 front-matter 时 ``metadata`` 为空字典、
        ``body`` 为原文。
    :raises FrontmatterParseError: 以 ``---`` 开头但找不到闭合分隔符。

    只支持最基本的 ``key: value`` 形式（不支持嵌套、列表、多行值）——这足以覆盖
    提示词文件与技能清单的必需字段（name / description）。需要更复杂结构时，
    正确的做法是把它放进正文用 markdown 表达，而不是在这里实现半个 YAML。

    .. note::
       判断依据是**文件第一行**是否为 ``---``。若正文里本来就有 markdown 分隔线，
       只要它不在第一行就不会被误判——这也是主流静态站点生成器的通行约定。
    """
    text = (raw_content or "").lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return {}, text

    end = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_DELIMITER:
            end = index
            break
    if end < 0:
        raise FrontmatterParseError(
            "front-matter 未闭合：文件以 '---' 开头但未找到结束分隔符"
        )

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        # 空行、注释行、非 key: value 行一律跳过——front-matter 里出现这些
        # 不影响正文抽取，没必要为此让整个文件失效。
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            metadata[key] = value

    return metadata, "\n".join(lines[end + 1:])


class PromptFileLoader:
    """提示词文件加载器。

    :param project_home: 项目根目录，作为相对路径的兜底基目录。
    :param system_config_dir: 系统配置目录，作为相对路径的首选基目录。

    两个基目录都可选：都没给时，只有绝对路径能被解析——这符合预期，因为拿不到
    任何基准时猜测相对路径的落点是危险的。

    用法::

        loader = PromptFileLoader(project_home="/repo", system_config_dir=cfg_dir)
        body = loader.load("prompts/planner.md")
    """

    def __init__(
        self,
        project_home: str | pathlib.Path | None = None,
        system_config_dir: str | pathlib.Path | None = None,
    ) -> None:
        self.project_home = self._to_path(project_home)
        self.system_config_dir = self._to_path(system_config_dir)

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def load(self, system_prompt_file: str | pathlib.Path | None = None) -> str:
        """加载提示词文件并返回**去掉 front-matter 的正文**。

        任何失败（没配、找不到、太大、解码失败、front-matter 格式错）都返回空串，
        由调用方决定如何回退——通常是回退到声明里内联的 ``system_prompt``。
        """
        _metadata, body = self.load_with_metadata(system_prompt_file)
        return body

    def load_with_metadata(
        self, system_prompt_file: str | pathlib.Path | None = None,
    ) -> tuple[dict[str, str], str]:
        """同上，但同时返回 front-matter 字典。

        返回 ``(metadata, body)``，顺序与 :func:`_parse_frontmatter` 一致，便于
        调用方用同一套解包写法。需要按 front-matter 里的字段做分支时（例如
        ``mode`` / ``version``）用这个方法，只想要正文时用 :meth:`load`。
        """
        path = self.resolve(system_prompt_file)
        if path is None:
            return {}, ""

        try:
            size = path.stat().st_size
        except OSError:
            logger.debug("提示词文件无法 stat，按未配置处理: %s", path, exc_info=True)
            return {}, ""
        if size > MAX_PROMPT_FILE_BYTES:
            logger.warning(
                "提示词文件过大已跳过：%s（%d 字节 > 上限 %d）",
                path, size, MAX_PROMPT_FILE_BYTES,
            )
            return {}, ""

        try:
            # utf-8-sig：自动吞掉 BOM，避免 BOM 混进正文开头被模型看到。
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except (OSError, UnicodeError):
            logger.warning("提示词文件读取失败: %s", path, exc_info=True)
            return {}, ""

        try:
            metadata, body = _parse_frontmatter(raw)
        except FrontmatterParseError:
            logger.warning(
                "提示词文件 front-matter 未闭合，整份跳过: %s", path, exc_info=True
            )
            return {}, ""

        return metadata, body.strip()

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    def resolve(
        self, system_prompt_file: str | pathlib.Path | None = None,
    ) -> pathlib.Path | None:
        """把「配置里写的路径」解析成实际存在的文件路径。

        :return: 存在且是文件的路径；解析不出或不存在时返回 ``None``。
        """
        raw = str(system_prompt_file or "").strip()
        if not raw:
            return None

        candidate = pathlib.Path(raw)
        if candidate.is_absolute():
            if candidate.is_file():
                return candidate
            logger.debug("提示词文件不存在（绝对路径）: %s", candidate)
            return None

        for base in (self.system_config_dir, self.project_home):
            if base is None:
                continue
            joined = base / candidate
            if joined.is_file():
                return joined

        logger.debug(
            "提示词文件未找到: %s（已试基目录 %s）",
            candidate,
            [str(b) for b in (self.system_config_dir, self.project_home) if b],
        )
        return None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _to_path(value: str | pathlib.Path | None) -> pathlib.Path | None:
        """把入参转成 ``Path``；``None`` 与非法值统一返回 ``None``。

        非法值（例如配置里写了个数字）不应让加载器构造失败——那只会导致后续
        所有提示词都取不到，而真正的错误只是某一处配置写错了。
        """
        if value is None:
            return None
        try:
            return pathlib.Path(value)
        except (TypeError, ValueError):
            logger.debug("提示词基目录不是合法路径，已忽略: %r", value)
            return None


def load_prompt_text(
    system_prompt_file: str | pathlib.Path | None,
    *,
    project_home: str | pathlib.Path | None = None,
    system_config_dir: str | pathlib.Path | None = None,
) -> str:
    """一次性加载提示词正文（无需自己构造 :class:`PromptFileLoader`）。

    给只有一处加载需求、且不需要 front-matter 的调用方省掉一层样板。
    """
    return PromptFileLoader(
        project_home=project_home,
        system_config_dir=system_config_dir,
    ).load(system_prompt_file)


__all__ = [
    "FrontmatterParseError",
    "PromptFileLoader",
    "MAX_PROMPT_FILE_BYTES",
    "load_prompt_text",
]
