import enum
import typing


class AgentRole(enum.StrEnum):

    GENERAL = "general"
    ORCHESTRATOR = "orchestrator"
    PLUGIN_ROOT = "plugin_root"
    SUB_AGENT = "sub_agent"

    DELEGATE = "general"
    ROLEPLAY = "general"
    SOLO = "general"
    GUARO="general"

    # ------------------------------------------------------------------
    # 角色判定
    # ------------------------------------------------------------------

    @classmethod
    def resolve(
        cls,
        role: typing.Any,
        slot: typing.Any = None,
    ) -> "AgentRole":
        """确定一个 agent 的最终角色。

        取值优先级：**显式声明 > 槽位声明 > 槽位形态推断 > 兜底 GENERAL**。

        * ``role`` 已经是 ``AgentRole``（或能解析成的字符串）时直接采用；
        * 否则看槽位自带 ``agent_role``；
        * 再否则按槽位的 ``agent_type`` 推断（``orchestrator`` → 编排者，
          ``plugin_root`` → 插件根）；
        * 都没有就落 ``GENERAL``——它同时也是 ``DELEGATE`` / ``ROLEPLAY`` /
          ``SOLO`` 的别名，语义上就是「普通 agent」。

        解析不出来的脏值（未知字符串、``None``）不会抛异常：角色只影响提示词
        与工具装配，为它中断整个装配流程不值得。

        :param role: 显式声明的角色，可为 ``None``。
        :param slot: 槽位声明（``plugin.domain.AgentSlot``），可为 ``None``。
        """
        resolved = cls._coerce(role)
        if resolved is not None:
            return resolved

        if slot is not None:
            resolved = cls._coerce(getattr(slot, "agent_role", None))
            if resolved is not None:
                return resolved

            agent_type = str(getattr(slot, "agent_type", "") or "").strip().lower()
            if agent_type == "orchestrator":
                return cls.ORCHESTRATOR
            if agent_type == "plugin_root":
                return cls.PLUGIN_ROOT

        return cls.GENERAL

    @classmethod
    def _coerce(cls, value: typing.Any) -> typing.Optional["AgentRole"]:
        """把任意入参转成 ``AgentRole``；转不了返回 ``None``（不抛）。"""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                try:
                    return cls(candidate)
                except ValueError:
                    return None
        return None

    @property
    def uses_mode_prompt_file(self) -> bool:
        """本角色的提示词是否来自「模式声明指定的提示词文件」。

        编排者与插件根的系统提示词由所属模式（mode）的声明文件决定，其余角色
        沿用各自的配置/兜底提示词。
        """
        return self in (AgentRole.ORCHESTRATOR, AgentRole.PLUGIN_ROOT)

    @property
    def is_root_like(self) -> bool:
        """是否承担「根 agent」职责（编排者 / 插件根）。"""
        return self.uses_mode_prompt_file
