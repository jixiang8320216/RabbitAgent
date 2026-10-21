"""信息查询类命令（TUI）。

本文件此前是**空壳占位**：``/skills``、``/agents``、``/plan`` 三条命令的
``execute`` 一律返回 ``CommandResult(success=True, message="xxx: scaffold
placeholder")``。

问题性质
--------
这是最危险的一类缺陷——**返回 success=True 却什么都没做**。用户在 TUI 里
敲 ``/agents``，看到的是一行「agents: scaffold placeholder」，
既没有报错也没有提示「该命令尚未实现」，与「已实现但列表为空」无法区分。

本轮把其中两条改为**读取真实运行时状态**：

- ``/agents``  —— 列出 ``app.agent_instances`` 里真实已创建的 agent
- ``/skills``  —— 列出配置里真实可用的 skills

``/plan`` 涉及模式切换（需要改动运行时状态并与 RoleContext 协同），
本轮**仍不实现**，但把文案改成明确的「未实现」且 ``success=False``，
如实反映状态，避免用户误认为操作已生效。
"""

from __future__ import annotations

import logging

from interfaces.commands.protocol import CommandProtocol, CommandResult

logger = logging.getLogger(__name__)


def _resolve_app(context):
    """从执行上下文里取回 RabbitApplication。

    ``TUIApplication._handle_command`` 传入的 context 同时带了
    ``app`` 与 ``application`` 两个键，都指向 rabbit_app；这里做能力探测，
    缺失时返回 None，由调用方决定降级文案。
    """
    if context is None:
        return None
    getter = getattr(context, "get", None)
    if callable(getter):
        return getter("app") or getter("application")
    return getattr(context, "app", None) or getattr(context, "application", None)


class SkillsCommand(CommandProtocol):
    name = "skills"
    description = "List available skills"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        app = _resolve_app(context)
        if app is None:
            return CommandResult(
                success=False,
                message="无法获取运行时上下文，/skills 需要在应用内执行。",
            )

        # ---- 优先走真实的 SkillRegistry ----
        # 此前本命令只查app.skills / app_config.skills 这两个配置字段，
        # 而 skill_registry 早已由 AgentFactory 持有（见
        # agent/service/agent_factory.py 的 skill_registry 形参），
        # 于是**磁盘上的技能包永远列不出来**。这里补上真实来源。
        registry = self._resolve_registry(app, context)
        if registry is not None:
            try:
                registry.load()
            except Exception as error:  # noqa: BLE001
                return CommandResult(
                    success=False,
                    message=f"技能注册表加载失败：{error}",
                )
            # 带参数时按名字/描述过滤，便于 `/skills <关键词>`
            keyword = (args[0] if args else "").strip().lower()
            if keyword:
                matched = [
                    pkg for pkg in registry.list_skills()
                    if keyword in pkg.name.lower()
                    or keyword in pkg.description.lower()
                ]
                if not matched:
                    return CommandResult(
                        success=True,
                        message=f"没有匹配 “{keyword}” 的技能。\n"
                                f"{registry.describe()}",
                    )
                lines = [f"匹配 “{keyword}” 的技能（{len(matched)} 个）："]
                for pkg in matched:
                    desc = f" —— {pkg.description}" if pkg.description else ""
                    lines.append(f"  - {pkg.name}{desc}")
                    lines.append(f"      路径：{pkg.path}")
                return CommandResult(success=True, message="\n".join(lines))
            return CommandResult(success=True, message=registry.describe())

        # ---- 回退：配置字段声明的 skills ----
        skills = None
        for attr in ("skills", "available_skills", "skill_configs"):
            candidate = getattr(app, attr, None)
            if candidate:
                skills = candidate
                break

        if not skills:
            config = getattr(app, "app_config", None)
            candidate = getattr(config, "skills", None) if config else None
            if candidate:
                skills = candidate

        if not skills:
            return CommandResult(
                success=True,
                message=(
                    "当前会话没有可用 skill。\n"
                    "提示：技能包需为 <项目根>/skills/<技能名>/SKILL.md，"
                    "头部含 YAML front-matter（name / description）。"
                ),
            )

        if isinstance(skills, dict):
            items = [f"  - {name}" for name in sorted(skills)]
        else:
            items = []
            for entry in skills:
                label = (
                    entry
                    if isinstance(entry, str)
                    else getattr(entry, "name", None) or getattr(entry, "id", str(entry))
                )
                items.append(f"  - {label}")

        return CommandResult(
            success=True,
            message=f"可用 skill（{len(items)} 个）：\n" + "\n".join(items),
        )

    @staticmethod
    def _resolve_registry(app, context):
        """从多处定位真实的 SkillRegistry 实例。"""
        candidates = [
            getattr(app, "skill_registry", None),
            getattr(app, "_skill_registry", None),
        ]
        factory = getattr(app, "_agent", None) or getattr(app, "agent_orchestrator", None)
        if factory is not None:
            candidates.append(getattr(factory, "skill_registry", None))
        runtime = getattr(app, "agent_runtime", None)
        if runtime is not None:
            candidates.append(getattr(runtime, "skill_registry", None))
        for candidate in candidates:
            if candidate is not None and hasattr(candidate, "list_skills"):
                return candidate
        return None


class AgentsCommand(CommandProtocol):
    name = "agents"
    description = "List available agents"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        app = _resolve_app(context)
        if app is None:
            return CommandResult(
                success=False,
                message="无法获取运行时上下文，/agents 需要在应用内执行。",
            )

        instances = getattr(app, "agent_instances", None) or {}
        if not isinstance(instances, dict):
            ids = list(instances or [])
        else:
            ids = list(instances.keys())

        if not ids:
            return CommandResult(
                success=True,
                message=(
                    "当前会话尚未创建任何 agent。\n"
                    "提示：agent 在会话初始化（init_session）时按配置创建，"
                    "可用 /status 查看会话状态。"
                ),
            )

        lines = [f"已注册 agent（{len(ids)} 个）："]
        for agent_id in sorted(str(item) for item in ids):
            detail = ""
            if isinstance(instances, dict):
                instance = instances.get(agent_id)
                spec = getattr(instance, "config", None) or getattr(
                    instance, "spec", None
                )
                name = getattr(spec, "name", None) if spec is not None else None
                if name:
                    detail = f"（{name}）"
            lines.append(f"  - {agent_id}{detail}")

        return CommandResult(success=True, message="\n".join(lines))


class PlanCommand(CommandProtocol):
    name = "plan"
    description = "Enter or manage plan mode"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        # 计划模式切换会改动运行时状态（当前模式 + RoleContext + 系统提示词），
        # 需要与 /mode 一起设计后实现。这里**如实回报未实现**，
        # 而不是返回 success=True 的占位文案让人误以为已切换。
        return CommandResult(
            success=False,
            message=(
                "/plan 尚未实现：计划模式切换需要与 /mode 协同改动运行时状态"
                "（当前模式 + RoleContext + 系统提示词），"
                "当前为占位实现，调用不会产生任何实际效果。"
            ),
        )