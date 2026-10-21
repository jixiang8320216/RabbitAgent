"""技能包解析器。

与 :mod:`skill.service.skill_registry` 的分工：

- ``skill_registry`` 负责**发现与索引**（扫目录、读 ``SKILL.md``、建映射）；
- 本模块负责**解析单个技能包目录**，产出结构化的解析结果，
  并提供「参数声明 / 副作用声明」的提取能力。

为什么单独拆一层
----------------
验收项 L1-2.3（参数校验）与 L1-2.5（副作用隔离）要求系统能回答两个问题：

1. 这个技能**声明了哪些参数**？调用时缺参应主动追问而非乱填；
2. 这个技能**声明了哪些副作用**（写文件 / 发消息 / 改配置）？

这两个问题都需要解析技能包的结构化信息，与「找技能」是不同职责。

技能包结构
----------
::

    <skill_dir>/
      SKILL.md          # 必需：front-matter + 正文
      scripts/          # 可选：脚本目录（声明为副作用时需用户确认）
      references/       # 可选：参考资料

front-matter 支持的字段::

    ---
    name: data_analysis          # 必需
    description: 数据分析        # 必需
    parameters:                  # 可选，参数声明
      - name: file
        type: string
        required: true
        description: 待分析的文件路径
    side_effects:                # 可选，副作用声明
      - type: file_write
        description: 写出分析结果到 output.csv
    allowed_tools: [read, write]  # 可选
    ---

解析器只做**解析与校验**，不执行技能、不加载脚本——执行由上层决定，
这样「声明」与「执行」分离，副作用可控。
"""

from __future__ import annotations

import pathlib
import typing

#: 支持的参数类型（与项目内部工具参数类型对齐）。
SUPPORTED_PARAMETER_TYPES = frozenset(
    {"string", "number", "integer", "boolean", "array", "object"}
)

#: 副作用类型分类。
SIDE_EFFECT_CATEGORIES = {
    "file_read": "只读文件",
    "file_write": "写入/修改文件",
    "file_delete": "删除文件",
    "network": "访问网络",
    "message": "发送消息/通知",
    "process": "执行子进程",
    "config_change": "修改配置",
}


class SkillParameter:
    """技能声明的一个参数。

    :param name: 参数名。
    :param type: 参数类型（限定在 :data:`SUPPORTED_PARAMETER_TYPES`）。
    :param required: 是否必填。
    :param description: 参数说明。
    :param default: 默认值。
    """

    __slots__ = ("name", "type", "required", "description", "default")

    def __init__(
        self,
        name: str,
        type: str = "string",
        required: bool = False,
        description: str = "",
        default: typing.Any = None,
    ) -> None:
        self.name = name
        self.type = type if type in SUPPORTED_PARAMETER_TYPES else "string"
        self.required = bool(required)
        self.description = description
        self.default = default

    def missing_from(self, provided: typing.Mapping[str, typing.Any]) -> bool:
        """判断该参数是否在给定入参中缺失且无默认值。

        这是 L1-2.3「缺参时主动追问而非乱填」的判定基础。
        """
        if self.name in provided and provided[self.name] is not None:
            return False
        return self.default is None

    def to_dict(self) -> dict[str, typing.Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
            "default": self.default,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        flag = "必填" if self.required else "可选"
        return f"SkillParameter({self.name!r}, {self.type}, {flag})"


class SideEffect:
    """技能声明的一个副作用。

    :param type: 副作用类型（见 :data:`SIDE_EFFECT_CATEGORIES`）。
    :param description: 说明。
    :param requires_confirm: 是否需要用户确认。
    """

    __slots__ = ("type", "description", "requires_confirm")

    #: 这些副作用在执行前必须取得用户确认。
    CONFIRM_REQUIRED = frozenset(
        {"file_write", "file_delete", "network", "message", "process",
         "config_change"}
    )

    def __init__(
        self,
        type: str,
        description: str = "",
        requires_confirm: bool | None = None,
    ) -> None:
        self.type = type
        self.description = description
        self.requires_confirm = (
            type in self.CONFIRM_REQUIRED
            if requires_confirm is None else bool(requires_confirm)
        )

    def to_dict(self) -> dict[str, typing.Any]:
        return {
            "type": self.type,
            "category": SIDE_EFFECT_CATEGORIES.get(self.type, "未分类"),
            "description": self.description,
            "requires_confirm": self.requires_confirm,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"SideEffect({self.type!r}, confirm={self.requires_confirm})"


class ParsedSkillPackage:
    """一个解析完成的技能包。

    :param name: 技能名。
    :param description: 一句话描述。
    :param instructions: 正文指令。
    :param directory: 技能目录。
    :param parameters: 声明的参数列表。
    :param side_effects: 声明的副作用列表。
    :param allowed_tools: 声明允许使用的工具名。
    :param warnings: 解析告警。
    """

    def __init__(
        self,
        name: str,
        description: str,
        instructions: str,
        directory: pathlib.Path,
        parameters: typing.Sequence[SkillParameter] = (),
        side_effects: typing.Sequence[SideEffect] = (),
        allowed_tools: typing.Sequence[str] = (),
        warnings: typing.Sequence[str] = (),
    ) -> None:
        self.name = name
        self.description = description
        self.instructions = instructions
        self.directory = directory
        self.parameters = list(parameters)
        self.side_effects = list(side_effects)
        self.allowed_tools = list(allowed_tools)
        self.warnings = list(warnings)

    # ------------------------------------------------------------------
    @property
    def required_parameters(self) -> list[SkillParameter]:
        return [p for p in self.parameters if p.required]

    @property
    def needs_confirm(self) -> bool:
        """是否存在需要用户确认的副作用（L1-2.5）。"""
        return any(e.requires_confirm for e in self.side_effects)

    def missing_parameters(
        self, provided: typing.Mapping[str, typing.Any]
    ) -> list[str]:
        """返回缺失且必填的参数名列表。

        验收 L1-2.3：缺参时应主动追问而非乱填——
        调用方拿到这个非空列表就应该向用户澄清，而不是自行编造默认值。
        """
        return [p.name for p in self.parameters
                if p.required and p.missing_from(provided)]

    def validate_arguments(
        self, provided: typing.Mapping[str, typing.Any]
    ) -> tuple[bool, list[str]]:
        """校验调用参数。

        :return: ``(是否合法, 问题列表)``。
        """
        problems: list[str] = []
        for name in self.missing_parameters(provided):
            problems.append(f"缺少必填参数：{name}")
        for param in self.parameters:
            if param.name not in provided:
                continue
            value = provided[param.name]
            if not _type_matches(param.type, value):
                problems.append(
                    f"参数 {param.name} 类型应为 {param.type}，"
                    f"实际为 {type(value).__name__}"
                )
        return (not problems, problems)

    def summary(self) -> str:
        lines = [f"技能：{self.name}"]
        if self.description:
            lines.append(f"描述：{self.description}")
        lines.append(f"参数：{len(self.parameters)} 个"
                     f"（必填 {len(self.required_parameters)} 个）")
        for param in self.parameters:
            flag = "必填" if param.required else "可选"
            lines.append(f"  - {param.name}（{param.type}, {flag}）"
                         f"{'：' + param.description if param.description else ''}")
        lines.append(f"副作用：{len(self.side_effects)} 项"
                     f"{'（含需确认项）' if self.needs_confirm else ''}")
        for effect in self.side_effects:
            need = "需确认" if effect.requires_confirm else "无需确认"
            lines.append(f"  - {effect.type}（{need}）"
                         f"{'：' + effect.description if effect.description else ''}")
        if self.allowed_tools:
            lines.append(f"允许工具：{', '.join(self.allowed_tools)}")
        if self.warnings:
            lines.append("解析告警：")
            lines.extend(f"  ! {w}" for w in self.warnings)
        return "\n".join(lines)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (f"ParsedSkillPackage({self.name!r}, "
                f"params={len(self.parameters)}, "
                f"effects={len(self.side_effects)})")


class SkillPackageParseError(ValueError):
    """技能包结构非法（缺 name/description 等）。"""


def parse_skill_package(
    skill_dir: str | pathlib.Path,
) -> ParsedSkillPackage:
    """解析一个技能包目录。

    :param skill_dir: 技能目录（内含 ``SKILL.md``）。
    :raises SkillPackageParseError: 缺 ``SKILL.md``、缺 name 等致命问题。
    :return: :class:`ParsedSkillPackage`；非致命问题记入 ``warnings``。
    """
    directory = pathlib.Path(skill_dir)
    manifest: pathlib.Path | None = None
    for candidate_name in ("SKILL.md", "skill.md"):
        candidate = directory / candidate_name
        if candidate.is_file():
            manifest = candidate
            break
    if manifest is None:
        raise SkillPackageParseError(f"技能目录缺少 SKILL.md：{directory}")

    raw = manifest.read_text(encoding="utf-8", errors="replace")
    meta, body = _split_front_matter(raw)
    warnings: list[str] = []

    name = str(meta.get("name") or directory.name).strip()
    if not name:
        raise SkillPackageParseError(f"技能包缺少 name：{manifest}")
    description = str(meta.get("description") or "").strip()
    if not description:
        warnings.append("技能包未声明 description，路由命中率会受影响")

    parameters = _parse_parameters(meta.get("parameters"), warnings)
    side_effects = _parse_side_effects(meta.get("side_effects"), warnings)
    allowed_tools = _parse_string_list(meta.get("allowed_tools"))

    # 目录里存在 scripts/ 但未声明副作用 -> 潜在风险，提醒确认
    scripts_dir = directory / "scripts"
    if scripts_dir.is_dir() and not side_effects:
        warnings.append(
            "存在 scripts/ 目录但未在 front-matter 声明 side_effects，"
            "执行前应按未知副作用处理"
        )

    return ParsedSkillPackage(
        name=name,
        description=description,
        instructions=body.strip(),
        directory=directory,
        parameters=parameters,
        side_effects=side_effects,
        allowed_tools=allowed_tools,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
def _split_front_matter(raw: str) -> tuple[dict[str, typing.Any], str]:
    """拆出 front-matter 字典与正文。

    支持两种写法::

        parameters: [{name: file, type: string, required: true}]
        parameters:
          - name: file
            type: string

    第一种（行内JSON/列表）整体解析，第二种逐行累积。
    """
    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end < 0:
        return {}, text

    head = lines[1:end]
    body = "\n".join(lines[end + 1:])

    meta: dict[str, typing.Any] = {}
    current_key: str | None = None
    current_list: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_list
        if current_key is not None and current_list:
            meta[current_key] = _parse_list_block(current_list)
        current_key = None
        current_list = []

    for line in head:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not line.startswith((" ", "\t")) and ":" in stripped:
            # 顶层键（无缩进）：先把上一段列表块收尾，再处理本键
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            flush()
            if not key:
                continue
            if not value:
                # 值在下一行（列表块形式）：记下键，继续收集缩进行
                current_key = key
                continue
            meta[key] = _coerce_scalar(value)
        elif current_key is not None:
            current_list.append(stripped)

    flush()
    return meta, body


def _parse_list_block(lines: list[str]) -> list[dict[str, typing.Any]]:
    """把 ``- key: value`` 形式的行块解析成 dict 列表。

    正确性要点：**只有遇到 ``- name: x``（带``-`` 前缀）才开启新条目**。
    上一版把「无 ``-`` 前缀的续行」也当成新条目判断，导致
    ``- name: file`` / ``type: string`` / ``required: true`` /
    ``- name: rows`` 被解析成 3 个条目且字段互相串位
    （实测file 变成「可选」、rows 变成「必填」，还多出一个重复项）。
    """
    items: list[dict[str, typing.Any]] = []
    current: dict[str, typing.Any] | None = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        # 续行（没有 "- " 前缀）归入当前条目
        is_new_item = stripped.startswith("-")
        payload = stripped.lstrip("-").strip() if is_new_item else stripped
        if ":" not in payload:
            continue
        key, _, value = payload.partition(":")
        key = key.strip()
        if not key:
            continue

        if is_new_item:
            # 新条目：先把上一条收尾
            if current:
                items.append(current)
            current = {}
        elif current is None:
            # 没有 "- " 起头的裸键，视为单条映射的第一个字段
            current = {}
        current[key] = _coerce_scalar(value.strip())

    if current:
        items.append(current)
    return items


def _coerce_scalar(value: str) -> typing.Any:
    """把字符串值转成合适的 Python 类型。"""
    text = value.strip().strip("'\"")
    if text.lower() in ("true", "yes"):
        return True
    if text.lower() in ("false", "no"):
        return False
    if text.lower() in ("null", "none", "~", ""):
        return None if text.lower() in ("null", "none", "~") else ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(p) for p in inner.split(",")]
    try:
        if text.lstrip("-").isdigit():
            return int(text)
        return float(text) if "." in text and text.replace(".", "").isdigit() else text
    except ValueError:
        return text


def _parse_parameters(
    raw: typing.Any, warnings: list[str]
) -> list[SkillParameter]:
    """解析参数声明，兼容多种写法。"""
    if raw in (None, "", [], {}):
        return []
    entries: list[typing.Any]
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        # 形如 parameters: {file: {type: string, required: true}}
        entries = [{"name": k, **(v if isinstance(v, dict) else {"type": v})}
                   for k, v in raw.items()]
    elif isinstance(raw, str):
        try:
            import json

            parsed = json.loads(raw)
            entries = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            warnings.append(f"parameters 无法解析：{raw[:60]!r}")
            return []
    else:
        warnings.append(f"parameters 类型异常：{type(raw).__name__}")
        return []

    params: list[SkillParameter] = []
    for entry in entries:
        if not isinstance(entry, dict):
            warnings.append(f"忽略非法参数声明：{entry!r}")
            continue
        pname = str(entry.get("name") or "").strip()
        if not pname:
            warnings.append(f"忽略缺 name 的参数声明：{entry!r}")
            continue
        ptype = str(entry.get("type") or "string").strip().lower()
        if ptype not in SUPPORTED_PARAMETER_TYPES:
            warnings.append(
                f"参数 {pname} 的类型 {ptype!r} 不在支持列表，"
                f"按 string 处理"
            )
        params.append(SkillParameter(
            name=pname,
            type=ptype,
            required=bool(entry.get("required", False)),
            description=str(entry.get("description") or "").strip(),
            default=entry.get("default"),
        ))
    return params


def _parse_side_effects(
    raw: typing.Any, warnings: list[str]
) -> list[SideEffect]:
    """解析副作用声明。"""
    if raw in (None, "", [], {}):
        return []
    entries: list[typing.Any]
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = [{"type": k, **(v if isinstance(v, dict) else {})}
                   for k, v in raw.items()]
    elif isinstance(raw, str):
        entries = [{"type": raw.strip()}]
    else:
        return []

    effects: list[SideEffect] = []
    for entry in entries:
        if isinstance(entry, str):
            effects.append(SideEffect(type=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        etype = str(entry.get("type") or "").strip()
        if not etype:
            warnings.append(f"忽略缺 type 的副作用声明：{entry!r}")
            continue
        if etype not in SIDE_EFFECT_CATEGORIES:
            warnings.append(
                f"副作用类型 {etype!r} 未在已知分类中"
                f"（{sorted(SIDE_EFFECT_CATEGORIES)}），"
                f"按需确认处理"
            )
        effects.append(SideEffect(
            type=etype,
            description=str(entry.get("description") or "").strip(),
            requires_confirm=entry.get("requires_confirm"),
        ))
    return effects


def _parse_string_list(raw: typing.Any) -> list[str]:
    if raw in (None, "", [], {}):
        return []
    if isinstance(raw, str):
        text = raw.strip().strip("[]")
        return [p.strip().strip("'\" ") for p in text.split(",") if p.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _type_matches(expected: str, value: typing.Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "object":
        return isinstance(value, dict)
    return True


__all__ = [
    "SkillParameter",
    "SideEffect",
    "ParsedSkillPackage",
    "SkillPackageParseError",
    "parse_skill_package",
    "SUPPORTED_PARAMETER_TYPES",
    "SIDE_EFFECT_CATEGORIES",
]