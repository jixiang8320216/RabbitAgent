"""根 agent（Plan Agent）配置加载器。

加载顺序（命中即止）：

1. 模式声明（``mode_registry.get(mode)`` -> ``RegisteredMode`` 包装，
   其属性转发自内部 ``ModeDeclaration``）里
   ``root_agent`` 指名的槽位 -> 该槽位的 ``system_prompt_file``；
2. ``<config_dir>/<mode>.md`` / ``.txt`` —— 模式专属提示词；
3. ``<config_dir>/root.md`` / ``root_agent.md`` / ``planner.md`` / ``plan.md``；
4. 内置兜底提示词（+ 项目系统提示词追加）。

注意：``ModelRegistry.get()`` 返回 ``RegisteredMode`` 包装对象，它通过
``__getattr__`` 把属性转发给内部 ``ModeDeclaration``，因此**既**可以直接当声明
使用，**也**可以通过 ``.declaration`` 取到原始声明。

任一环节因依赖缺失而失败时继续走下一级；只有宿主依赖（``config_manager`` /
``mode_registry``）整体缺失才返回 ``None``——根 agent 缺失由上层决定如何提示，
而不是让进程崩溃（与
:mod:`app.application.orchestrators.agent_configuration_loading` 的降级约定一致）。
"""

from __future__ import annotations

import logging
import pathlib
import typing

import domains.agent.domain.entities.root_agent_config as root_agent_config
import domains.agent.application.registry.model_registry as mode_registry_module

if typing.TYPE_CHECKING:  # 仅供类型注解，运行时不引入（避免装配期循环/重依赖）
    import domains.configuration.infrastructure.adapter.config_manager as configuration

logger = logging.getLogger(__name__)

__all__ = ["RootAgentLoader"]

_PROMPT_SUFFIXES: tuple[str, ...] = (".md", ".txt")
_GENERIC_PROMPT_STEMS: tuple[str, ...] = ("root", "root_agent", "planner", "plan")
_DEFAULT_NAME = "planner"
DEFAULT_ROOT_AGENT_MODEL = "gpt-4-turbo"

DEFAULT_SYSTEM_PROMPT = (
    "You are the planning agent of a multi-agent system.\n"
    "Break the user's request into concrete steps, delegate work to the\n"
    "available sub-agents or tools, and report a concise final answer."
)


class RootAgentLoader:

    def __init__(
        self,
        *,
        config_dir: pathlib.Path | str | None,
        config_manager: "configuration.ConfigManager",
        mode_registry: mode_registry_module.ModelRegistry,
        project_home: typing.Optional[str] = None,
    ) -> None:
        self.config_dir = pathlib.Path(config_dir) if config_dir is not None else None
        self.config_manager = config_manager
        self.mode_registry = mode_registry
        self.project_home = project_home

    # -- 加载 ------------------------------------------------------------
    def load(self, mode: str = "delegate") -> typing.Optional[root_agent_config.RootAgentConfig]:
        """加载根 agent 配置。

        :param mode: 应用模式名，用于在模式注册表里查声明、并拼提示词文件名。
        :return: 根 agent 配置；宿主依赖缺失时返回 ``None``。
        """
        if self.config_manager is None or self.mode_registry is None:
            logger.warning(
                "root agent loader: config manager or mode registry is unavailable, "
                "root agent configuration skipped"
            )
            return None

        default_model = self._default_model()
        declaration = self.mode_registry.get(mode)

        # 1) 模式声明里根槽位指定的提示词文件
        if declaration is not None:
            # ``ModelRegistry.get()`` 返回 ``RegisteredMode`` 包装对象，它通过
            # ``__getattr__`` 把属性转发给内部 ``ModeDeclaration``，
            # 因此这里按声明使用即可。
            root_slot = self._root_slot(declaration)
            prompt_file = str(getattr(root_slot, "system_prompt_file", "") or "")
            if prompt_file:
                config = self._load_from_file(prompt_file, default_model, mode=mode)
                if config is not None:
                    return config
                logger.debug(
                    "root agent prompt file %r declared by mode %r is unusable, "
                    "falling back to directory scan",
                    prompt_file,
                    mode,
                )
        else:
            logger.debug("mode %r is not registered, falling back to directory scan", mode)

        # 2/3) 目录扫描；4) 内置兜底
        prompt, prompt_path = self._resolve_prompt(mode)
        return self._build_config(
            name=getattr(declaration, "name", "") or _DEFAULT_NAME,
            description=self._description(declaration),
            system_prompt=prompt,
            model=default_model,
            mode=str(mode or ""),
            prompt_path=prompt_path,
            source="prompt-file" if prompt_path is not None else "default",
        )

    @staticmethod
    def _build_config(**payload: typing.Any) -> root_agent_config.RootAgentConfig:
        """按 ``RootAgentConfig`` **当前实际声明的字段**投递构造参数。

        根配置模型正在施工，字段集合变过几轮（``model_id`` ↔ ``model``、
        ``mode`` / ``prompt_path`` / ``source`` 时有时无），而它是
        ``extra="forbid"``：多传或错名一个键就会整体校验失败，表现为启动直接
        ``Fatal error``。所以这里做两件事：

        1. 只有模型声明了的字段才往下传；
        2. 模型只认 ``model`` 时，把 ``model_id`` 的值搬过去（反之亦然）。

        这样模型字段再调整，loader 不用跟着改。
        """
        declared = set(root_agent_config.RootAgentConfig.model_fields)
        if "model_id" in payload and "model_id" not in declared and "model" in declared:
            payload["model"] = payload.pop("model_id")
        if "model" in payload and "model" not in declared and "model_id" in declared:
            payload["model_id"] = payload.pop("model")
        return root_agent_config.RootAgentConfig(
            **{key: value for key, value in payload.items() if key in declared}
        )

    # -- 依赖读取 ---------------------------------------------------------
    def _default_model(self) -> str:
        """从配置管理器读默认模型；缺失/类型不符时用内置常量兜底。"""
        configured_model = self.config_manager.get_property("model", DEFAULT_ROOT_AGENT_MODEL)
        return configured_model if isinstance(configured_model, str) and configured_model else DEFAULT_ROOT_AGENT_MODEL

    @staticmethod
    def _root_slot(declaration: typing.Any) -> typing.Any:
        """取模式声明里 ``root_agent`` 指名的槽位；取不到返回 ``None``。"""
        agent_slots = getattr(declaration, "agent_slots", None) or {}
        root_agent_name = str(getattr(declaration, "root_agent", "") or "")
        if not root_agent_name:
            return None
        try:
            return agent_slots.get(root_agent_name)
        except AttributeError:  # 非映射类型的防御
            return None

    @staticmethod
    def _description(declaration: typing.Any) -> str:
        if declaration is None:
            return "Root planning agent"
        return str(getattr(declaration, "description", "") or "Root planning agent")

    # -- 文件加载 ---------------------------------------------------------
    def _load_from_file(
        self,
        prompt_file: str,
        default_model: str,
        *,
        mode: str = "",
    ) -> typing.Optional[root_agent_config.RootAgentConfig]:
        """按槽位声明的提示词文件构造配置；文件不可读返回 ``None``。

        路径解析：绝对路径直接用；相对路径相对 ``config_dir``（未配置时相对
        ``project_home``）。
        """
        path = pathlib.Path(prompt_file)
        if not path.is_absolute():
            base = self.config_dir
            if base is None and self.project_home:
                base = pathlib.Path(self.project_home)
            path = base / path if base is not None else path
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            logger.debug("root agent prompt file unreadable: %s", path, exc_info=True)
            return None
        if not text:
            return None
        logger.debug("root agent prompt loaded from %s", path)
        return self._build_config(
            name=_DEFAULT_NAME,
            description="Root planning agent",
            system_prompt=text,
            model=default_model,
            mode=str(mode or ""),
            prompt_path=path,
            source="prompt-file",
        )

    # -- 提示词兜底解析 ---------------------------------------------------
    def _resolve_prompt(self, mode: str) -> tuple[str, pathlib.Path | None]:
        """返回 ``(提示词正文, 来源路径)``；未命中文件时走内置兜底。"""
        for candidate in self._candidate_prompt_paths(mode):
            try:
                if not candidate.is_file():
                    continue
                text = candidate.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                logger.debug("root agent prompt unreadable: %s", candidate, exc_info=True)
                continue
            if text:
                logger.debug("root agent prompt loaded from %s", candidate)
                return text, candidate
        if self.config_dir is not None:
            logger.debug(
                "no root agent prompt found under %s, using built-in fallback",
                self.config_dir,
            )
        return self._build_default_prompt(), None

    def _candidate_prompt_paths(self, mode: str) -> list[pathlib.Path]:
        """候选路径：模式专属文件名优先，其次通用文件名。"""
        if self.config_dir is None:
            return []
        normalized_mode = str(mode or "").strip()
        candidates: list[pathlib.Path] = []
        if normalized_mode:
            for suffix in _PROMPT_SUFFIXES:
                candidates.append(self.config_dir / f"{normalized_mode}{suffix}")
        for stem in _GENERIC_PROMPT_STEMS:
            for suffix in _PROMPT_SUFFIXES:
                candidates.append(self.config_dir / f"{stem}{suffix}")
        return candidates

    def _build_default_prompt(self) -> str:
        """内置兜底提示词；项目提示词以独立段落追加在后面。"""
        return DEFAULT_SYSTEM_PROMPT

    def __repr__(self) -> str:
        return f"RootAgentLoader(config_dir={self.config_dir!r}, project_home={self.project_home!r})"


def _read_attr(target: typing.Any, *names: str) -> typing.Any:
    """按顺序读取对象上第一个存在的属性；全部缺失返回 ``None``。"""
    for name in names:
        value = getattr(target, name, None)
        if value is not None:
            return value
    return None
