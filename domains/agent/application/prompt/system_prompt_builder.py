import inspect
import logging
import pathlib
import typing

import domains.agent.application.prompt.prompt_file_loader as prompt_file_loader
import domains.context.application.context_optimizer as context_optimizer_module
import domains.agent.domain.entities.agent_runtime as agent_runtime_module
import domains.agent.infrastructure.static_environment_context as static_environment_context
import domains.agent.domain.entities.root_agent_config as root_agent_config_module
import domains.agent.domain.entities.agent_role as agent_role
import domains.agent.application.prompt.skill_prompt_context as skill_prompt_context
import domains.agent.domain.aggregates.execution_context as execution_context

if typing.TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，运行期不导入
    import domains.configuration.infrastructure.adapter.config_manager as configuration
    import domains.skill.service.skill_registry as skill_registry_module
    import domains.configuration.domain.runtime_state.system_resources as system_resource_module


logger = logging.getLogger(__name__)

# 内置兜底提示词：与 root_agent_loader.DEFAULT_SYSTEM_PROMPT 同源的精简版，
# 仅在「模式提示词文件读不到、且 RootAgentConfig.system_prompt 也为空」时启用。
_DEFAULT_PLAN_PROMPT = (
    "You are the planning agent of a multi-agent system.\n"
    "Break the user's request into concrete steps, delegate work to the\n"
    "available sub-agents or tools, and report a concise final answer."
)


def _safe_str(value: typing.Any) -> str:
    """把任意值安全转成 ``str``；``None``/空返回空串，绝不抛。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - 转字符串失败不应阻断装配
        return ""


class SystemPromptBuilder:
    """系统提示词构造器。

    把「拼装系统提示词所需的全部素材」一次性收齐并持有：

    - ``config_manager``：配置管理器，取运行期配置用。
    - ``context_optimizer``：上下文优化器（由
      ``agent_context_adapter.ContextOptimizerBootstrapper.bootstrap`` 产出，
      拿不到时可能是 ``None``）。
    - ``current_user_request``：当前这一轮的用户请求原文。
    - ``skill_registry``：技能注册表，用于展开可用技能描述。
    - ``rabbit_skill_md``：聚合后的技能说明 markdown。
    - ``instruction_resources``：指令资源（沿用调用点既有拼写）。

    装配位置：``AgentFactory.__init__``。依赖全部可选——缺哪一项都只是对应的
    素材缺席，不应让构造器本身构造失败。
    """

    def __init__(
        self,
        *,
        config_manager: configuration.ConfigManager,
        context_optimizer: context_optimizer_module.ContextOptimizer|None = None,
        current_user_request: typing.Optional[str] = "",
        skill_registry: typing.Optional[skill_registry_module.SkillRegistry] = None,
        rabbit_skills_md: typing.Optional[str] = None,
        instruction_resources: typing.Sequence[system_resource_module.InstructionResource],
    ) -> None:
        self.config_manager = config_manager
        self.context_optimizer = context_optimizer
        self.current_user_request = current_user_request
        self.skill_registry = skill_registry
        self.rabbit_skills_md = rabbit_skills_md
        self.instruction_resources = instruction_resources

    # ------------------------------------------------------------------
    # 构造系统提示词
    # ------------------------------------------------------------------

    async def build(
        self,
        role: agent_role.AgentRole,
        sub_agent: agent_runtime_module.AgentRuntime | root_agent_config_module.RootAgentConfig,
        *,
        system_prompt_file: typing.Optional[str] = None,
        project_home: typing.Optional[str] = None,
        system_config_dir: typing.Optional[pathlib.Path] = None,
        additional_sections: typing.Optional[typing.List[str]] = None,
        optimize: bool = True,
    ) -> str:
        if role.uses_mode_prompt_file:
            prompt= await self._build_for_plan_like(
                role=role,
                sub_agent=sub_agent,
                system_prompt_file=system_prompt_file,
                project_home= project_home,
                system_config_dir =system_config_dir,
            )
        else:
            if not isinstance(sub_agent, agent_runtime_module.AgentRuntime):
                raise TypeError("sub_agent must be an instance of AgentRuntime")
            prompt =await self.build_prompt_for_sub_agent(
                sub_agent=sub_agent,
                project_home=project_home,
                system_config_dir=system_config_dir,
                optimize=optimize,
                additional_sections=additional_sections,
            )
        if role.uses_mode_prompt_file and additional_sections:
            prompt = static_environment_context.append_prompt_sections(prompt, additional_sections)
        return self._append_creation_context(
            system_prompt=prompt,
            role=role,
            sub_agent=sub_agent,
            project_home=project_home,
        )

    # ------------------------------------------------------------------
    # 模式提示词文件型（编排者 / 插件根）
    # ------------------------------------------------------------------

    async def _build_for_plan_like(
        self,
        *,
        role: agent_role.AgentRole,
        sub_agent: agent_runtime_module.AgentRuntime| root_agent_config_module.RootAgentConfig,
        system_prompt_file: typing.Optional[str] ,
        project_home: typing.Optional[str] = None,
        system_config_dir: typing.Optional[pathlib.Path] ,
    ) -> str:
        loader= prompt_file_loader.PromptFileLoader(
            project_home= project_home,
            system_config_dir=system_config_dir,
        )
        content = loader.load(system_prompt_file=system_prompt_file) if system_prompt_file else ""
        loaded_filename= system_prompt_file if content else None
        if not content:
            content = sub_agent.system_prompt or ""
        if content:
            instructions_header=(
                f"#SYSTEM INSTRUCTIONS (from {loaded_filename})\n\n"
                if loaded_filename
                else "# SYSTEM INSTRUCTIONS\n\n"
            )
            content=f"{instructions_header}{content}"
        return content

    # ------------------------------------------------------------------
    # 子 agent 型
    # ------------------------------------------------------------------

    async def _extract_sub_agent_base_prompt(
        self,
        sub_agent: agent_runtime_module.AgentRuntime,
        *,
        project_home: typing.Optional[str] = None,
        system_config_dir: typing.Optional[pathlib.Path] = None,
    ) -> str:
        """取子 agent 的基础提示词：优先提示词文件，回退到声明的 ``system_prompt``。

        与 :meth:`_build_for_plan_like` 的文件加载逻辑保持一致（同一套
        ``PromptFileLoader``、同样的 ``#SYSTEM INSTRUCTIONS`` 头）；加载失败只记
        debug 并回退——提示词素材缺席不该让 agent 装配失败。
        """
        slot= sub_agent.agent_slot if sub_agent.agent_role is agent_role.AgentRole.SUB_AGENT else None
        slot_prompt_file = slot.system_prompt_file if slot is not None else ""
        if slot_prompt_file:
            loader= prompt_file_loader.PromptFileLoader(
                project_home= project_home,
                system_config_dir= system_config_dir,
            )
            slot_prompt = loader.load(slot_prompt_file)
            if slot_prompt:
                return slot_prompt
        if sub_agent.system_prompt:
            return sub_agent.system_prompt

        skill_dir = self._get_sub_agent_skill_dir(sub_agent)
        if skill_dir:
            skill_md_path= skill_dir/"SKILL.md"
            if skill_md_path.exists():
                try:
                    raw_content= skill_md_path.read_text(encoding="utf-8")
                    try:
                        _metadata,body= prompt_file_loader._parse_frontmatter(raw_content)
                        stripped_chars = len(raw_content)-len(body)
                        return body
                    except prompt_file_loader.FrontmatterParseError as exc:
                        return ""
                except Exception as exc:
                    logger.error("error parsing frontmatter: %s", exc)
        return ""

    async def build_prompt_for_sub_agent(
        self,
        *,
        sub_agent: agent_runtime_module.AgentRuntime,
        project_home: typing.Optional[str] = None,
        system_config_dir: typing.Optional[pathlib.Path]|None = None,
        optimize: bool = True,
        add_plan_instructions: bool |None=None,
        additional_sections: typing.Optional[typing.List[str]] = None,
    ) -> str:
        role= sub_agent.agent_role
        uses_mode_prompt_file = role is not None and role.uses_mode_prompt_file
        if role is not None and uses_mode_prompt_file:
            logging.info(" building mod root prompt for %s", sub_agent.name)
        prompt_parts=[]
        explicit_prompt= await self._extract_sub_agent_base_prompt(
            sub_agent,
            project_home=project_home,
            system_config_dir=system_config_dir,
        )
        if explicit_prompt:
            prompt_parts.append(explicit_prompt)

        if additional_sections:
            for section in additional_sections:
                if section:
                    prompt_parts.append(section)

        base_prompt ="\n\n---\n\n".join(prompt_parts)
        if optimize and self.context_optimizer and self.current_user_request:
            try:
                base_prompt =await self._optimize_system_prompt(sub_agent, base_prompt)
            except Exception as e:
                logger.error(e)
        agent_kind="Mode Root" if uses_mode_prompt_file else "Configured Agent"
        return base_prompt

    # ------------------------------------------------------------------
    # 创建上下文（身份 + 运行环境）
    # ------------------------------------------------------------------

    def _append_creation_context(
        self,
        system_prompt: str,
        *,
        role: agent_role.AgentRole,
        sub_agent: agent_runtime_module.AgentRuntime| root_agent_config_module.RootAgentConfig,
        project_home: typing.Optional[str],
    ) -> str:
        runtime_agent = sub_agent if isinstance(sub_agent, agent_runtime_module.AgentRuntime) else None
        def _build_static_skill_runtime_context()->str:
            if runtime_agent is None:
                return ""
            return self.build_skill_runtime_context(runtime_agent)
        prompt= static_environment_context.append_static_agent_context(
            system_prompt=system_prompt,
            agent_name=sub_agent.name,
            skill_summary_provider=(
                lambda :(
                    skill_prompt_context.build_skill_summary_context(
                        runtime_agent=runtime_agent,
                        skill_registry=self.skill_registry,
                    )
                    if runtime_agent is not None
                    else ""
                )
            ),
            skill_runtime_context_provider= _build_static_skill_runtime_context,
            project_home=project_home,
            session_id= execution_context.get_session_id(),
        )
        return static_environment_context.append_prompt_sections(
            prompt,
            self._build_global_knowledge_sections(
                project_home=project_home,
                agent_name=sub_agent.name,
                role=role,
                load_agents_md=sub_agent.load_agents_md,
            )
        )

    def _build_global_knowledge_sections(
        self,
        *,
        project_home: typing.Optional[str],
        agent_name: str,
        role: agent_role.AgentRole,
        load_agents_md: bool,
    ) -> list[str]:
        """取项目级全局知识段落（``AGENTS.md`` / ``INSTRUCTIONS.md``）。

        ``agent_name`` 与 ``role`` 目前只作留痕（不同 agent 的知识块将来可能
        分桶），是否加载由 ``load_agents_md`` + ``project_home`` 决定：开关没开、
        或项目里根本没有这些文件时返回空列表。

        全局知识是增强素材：读不到就整段不拼，绝不让一个缺省文件打断装配。
        """
        if not load_agents_md or not project_home:
            return []
        try:
            root = pathlib.Path(project_home)
        except (TypeError, ValueError):
            logger.debug("invalid project_home for global knowledge: %r", project_home)
            return []

        sections: list[str] = []
        for filename in ("AGENTS.md", "INSTRUCTIONS.md"):
            for base in (root / ".rabbit", root):
                path = base / filename
                try:
                    content = path.read_text(encoding="utf-8").strip()
                except (OSError, UnicodeDecodeError):
                    continue
                if content:
                    sections.append(
                        f"# PROJECT KNOWLEDGE (from {filename})\n\n{content}"
                    )
                break
        return sections

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _resolve_prompt_file(
        self,
        system_prompt_file: typing.Optional[str],
        project_home: typing.Optional[str],
        system_config_dir: typing.Optional[pathlib.Path],
    ) -> str:
        """按 ``root_agent_loader`` 同款规则解析并读取提示词文件。

        绝对路径直用；相对路径依次相对 ``system_config_dir`` → ``project_home``；
        读不到 / 解码失败返回空串（不抛）。
        """
        raw = _safe_str(system_prompt_file).strip()
        if not raw:
            return ""
        path = pathlib.Path(raw)
        if not path.is_absolute():
            base: typing.Optional[pathlib.Path] = system_config_dir
            if base is None and project_home:
                try:
                    base = pathlib.Path(project_home)
                except (TypeError, ValueError):
                    base = None
            if base is None:
                return ""
            path = base / path
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.debug("prompt file unreadable: %s", path, exc_info=True)
            return ""
        return text.strip()

    def _instruction_sections(self) -> list[str]:
        """取指令资源内容；每条缺 ``content`` 或内容为空都安全跳过。"""
        sections: list[str] = []
        for resource in self.instruction_resources or []:
            text = _safe_str(getattr(resource, "content", ""))
            if text:
                sections.append(text)
        return sections

    async def _optimize_prompt(self, prompt: str) -> str:
        """上下文优化（可选）。

        支持三种返回形态：``str`` / 带 ``optimized_prompt`` · ``content`` ·
        ``prompt`` · ``text`` 属性的对象 / 协程结果；任何失败都回落未优化版本。
        """
        optimizer = self.context_optimizer
        if optimizer is None or not hasattr(optimizer, "optimize"):
            return prompt
        try:
            result = optimizer.optimize(prompt)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, str) and result:
                return result
            for attr in ("optimized_prompt", "content", "prompt", "text"):
                value = getattr(result, attr, None)
                if isinstance(value, str) and value:
                    return value
        except Exception:  # noqa: BLE001 - 优化失败不应阻断装配
            logger.debug("context optimize failed; keeping original prompt", exc_info=True)
        return prompt



