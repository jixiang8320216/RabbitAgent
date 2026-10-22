from __future__ import annotations

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
# ``InstructionMemoryType`` 在运行期就要取（``_build_global_knowledge_sections``
# 按作用域过滤指令资源），不能只放在 ``TYPE_CHECKING`` 块里。
import domains.hook.domain.inputs as hook_inputs

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
    - ``rabbit_skills_md``：聚合后的技能说明 markdown。
    - ``instruction_resources``：指令资源（沿用调用点既有拼写）。

    装配位置：``AgentFactory.__init__``。依赖全部可选——缺哪一项都只是对应的
    素材缺席，不应让构造器本身构造失败。

    .. note::
       ``skills_md`` 与 ``rabbit_skills_md`` 是同一份素材的两个拼写。
       整条装配链（``SystemResources.rabbit_skills_md`` →
       ``AgentOrchestrator._system_rabbit_skills_md`` →
       ``AgentBootstrap`` → ``AgentFactory``）统一用 ``rabbit_skills_md``；
       此前本类的形参却写成 ``skills_md``，两边对不上，
       ``AgentFactory.__init__`` 传 ``rabbit_skills_md=`` 时直接抛
       ``TypeError: SystemPromptBuilder.__init__() got an unexpected keyword
       argument 'rabbit_skills_md'``，TUI 首屏都起不来。
       现在以 ``rabbit_skills_md`` 为准，``skills_md`` 保留为兼容别名。
    """

    def __init__(
        self,
        *,
        config_manager: configuration.ConfigManager,
        context_optimizer: context_optimizer_module.ContextOptimizer|None = None,
        current_user_request: typing.Optional[str] = "",
        skill_registry: typing.Optional[skill_registry_module.SkillRegistry] = None,
        rabbit_skills_md: typing.Optional[str] = None,
        skills_md: typing.Optional[str] = None,
        instruction_resources: typing.Sequence[system_resource_module.InstructionResource]|None=None,
    ) -> None:
        self.config_manager = config_manager
        self.context_optimizer = context_optimizer
        self.current_user_request = current_user_request
        self.skill_registry = skill_registry
        # 两个拼写指向同一份素材；以 rabbit_skills_md 为准，skills_md 仅作兼容。
        self.rabbit_skills_md = rabbit_skills_md if rabbit_skills_md is not None else skills_md
        self.skills_md = self.rabbit_skills_md
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
        optimize: bool = False,
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
                logger.warning("[SYSTEM_PROMPT_BUILDER]Optionzation failed for %s:%s",sub_agent.name, e,exc_info=True)
        agent_kind="Mode Root" if uses_mode_prompt_file else "Configured Agent"
        return base_prompt

    def build_skill_runtime_context(
        self,
        runtime_agent: agent_runtime_module.AgentRuntime,
    ) -> str:
        """技能运行时上下文：技能在磁盘上的位置，供模型按需读全文。

        .. note::
           此前本方法是**只有调用点、没有定义**的空缺：``_append_creation_context``
           里调到它时抛 ``AttributeError``，而 ``static_environment_context``
           的 ``_safe_provider`` 会把一切异常兜成空串——于是「技能运行时上下文」
           永远是空的，且**没有任何日志**，静态看代码完全正常。

           现在转发到 ``skill_prompt_context``——真正的渲染逻辑在那里，
           本类只负责把 ``skill_registry`` 递过去。
        """
        return skill_prompt_context.build_skill_runtime_context(
            runtime_agent,
            self.skill_registry,
        )

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
        """重构：采用注入资源+四级分类（.rabbit->.app））
        """
        if not load_agents_md :
            return []

        prompt_parts: typing.List[str] = []
        use_knowledge = self._instruction_content(
            {hook_inputs.InstructionMemoryType.USER}
        )

        if use_knowledge:
            prompt_parts.append(f"# USER GLOBAL KNOWLEDGE\n\n {use_knowledge}")

        #project/local/managed 级别(窄作用域)

        project_knowledge= self._project_instruction_content(project_home)
        if project_knowledge:
            prompt_parts.append(f"#PROJECT GLOBAL  KNOWLEDGE\n\n {project_knowledge}")
        return prompt_parts

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

    def _instruction_content(
        self,
        memory_types: typing.Optional[typing.Iterable[hook_inputs.InstructionMemoryType]]=None,
    ) -> str:
        """按作用域聚合指令资源的内容。

        :param memory_types: 只取这些作用域的指令（如 ``{USER}``）；
                             传 ``None`` 表示不限作用域。
        :return: 聚合后的正文；无匹配资源时返回空串。

        .. note::
           本方法此前**只有调用点、没有定义**——``_build_global_knowledge_sections``
           调到它时抛 ``AttributeError: 'SystemPromptBuilder' object has no
           attribute '_instruction_content'``，于是每次构造系统提示词都在
           「用户级全局知识」这一步崩掉，agent 回合直接失败。

           素材来源就是构造时注入的 ``instruction_resources``：每条资源自带
           ``memory_type``（User/Project/Local/Managed）与 ``content``。
        """
        wanted: set[object]|None = None
        if memory_types is not None:
            wanted = {str(getattr(item, "value", item)) for item in memory_types}
        chunks: list[str] = []
        for resource in self.instruction_resources or []:
            if wanted is not None:
                memory_type = getattr(resource, "memory_type", None)
                if str(getattr(memory_type, "value", memory_type)) not in wanted:
                    continue
            text = _safe_str(getattr(resource, "content", ""))
            if text:
                chunks.append(text)
        return "\n\n".join(chunks)

    def _project_instruction_content(self, project_home: typing.Optional[str]) -> str:
        """取项目级（``PROJECT`` 作用域）的指令内容。

        ``project_home`` 参与筛选：``InstructionResource`` 里带
        ``trigger_file_path`` / ``parent_file_path`` 的资源若不在该项目目录下，
        不应被拼进系统提示词。
        """
        wanted = str(
            getattr(hook_inputs.InstructionMemoryType.PROJECT, "value",
                    hook_inputs.InstructionMemoryType.PROJECT)
        )
        chunks: list[str] = []
        for resource in self.instruction_resources or []:
            memory_type = getattr(resource, "memory_type", None)
            if str(getattr(memory_type, "value", memory_type)) != wanted:
                continue
            if project_home:
                path = _safe_str(getattr(resource, "file_path", ""))
                if path:
                    try:
                        resolved = pathlib.Path(path).resolve()
                        project_root = pathlib.Path(project_home).resolve()
                    except (OSError, ValueError):
                        resolved = None
                        project_root = None
                    if resolved is not None and project_root is not None:
                        if not resolved.is_relative_to(project_root):
                            continue
            text = _safe_str(getattr(resource, "content", ""))
            if text:
                chunks.append(text)
        return "\n\n".join(chunks)

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



