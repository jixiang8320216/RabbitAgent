
import inspect
import typing

import domains.configuration.domain.model.llm_config as llm_config_module
import domains.configuration.domain.runtime_state.execution_context as execution_context_module


class ExpertCatalogApplication(typing.Protocol):

    @property
    def execution_context(self)-> execution_context_module.ExecutionContext|None:...

    @property
    def llm_config(self)-> llm_config_module.LLMConfig: ...



class ExpertCallApplicationPort(ExpertCatalogApplication, typing.Protocol):

    current_default_agent_id: str|None


class ExpertCallService:
    """委派（call_subagent）能力：既是一个工具，也是一段提示词。

    同一个服务同时喂两条链路：

    * :meth:`call_subagent` 被注册成工具（``is_agent=True``），模型可以直接
      委派任务给别的 agent；
    * :meth:`build_prompt_section` 生成提示词片段，告诉模型「现在能委派给谁」——
      只给工具不给清单，模型只能靠猜名字。

    ``sub_agent`` 上的 ``allowed_agents`` 是白名单；没声明时退回宿主
    ``agent_runtimes`` 里的全部名字。

    .. note::
       委派的执行链路（调度器 / 结果回收）尚未完全打通，因此 :meth:`call_subagent`
       在当前阶段会优先尝试真实运行时入口，拿不到就返回**诊断文本**而不是抛异常：
       工具报错会让模型失去「换个 agent 试试」的机会，返回可读文案更利于恢复。
    """

    def __init__(self, app_instance: typing.Any, sub_agent: typing.Any = None) -> None:
        self.app_instance = app_instance
        self.sub_agent = sub_agent

    # --- 可见的委派目标 -------------------------------------------------
    def available_agent_names(self) -> tuple[str, ...]:
        """本次可以委派的 agent 名（去重、保序）。"""
        declared = self._text_tuple(
            getattr(self.sub_agent, "allowed_agents", None)
            or getattr(self.sub_agent, "alowwed_agents", None)
        )
        if declared:
            return declared
        runtimes = getattr(self.app_instance, "agent_runtimes", None)
        if isinstance(runtimes, typing.Mapping):  # type: ignore[arg-type]
            return self._text_tuple(list(runtimes.keys()))
        return ()

    def build_prompt_section(self) -> str:
        """提示词片段：列出可委派的 agent 与调用方式。"""
        names = self.available_agent_names()
        if not names:
            return ""
        listing = "\n".join(f"- {name}" for name in names)
        return (
            "## 委派\n"
            "需要别的角色完成时，用 call_subagent 工具委派，参数：\n"
            "  agent_name: 目标 agent 名；message: 要它做的事。\n"
            "当前可委派的 agent：\n"
            f"{listing}\n"
        )

    # --- 工具入口 -------------------------------------------------------
    async def call_subagent(
        self,
        agent_name: str = "",
        message: str = "",
        *,
        name: str | None = None,
        task: str | None = None,
        **extra: typing.Any,
    ) -> str:
        """委派任务给另一个 agent。

        参数名做了宽松处理：模型可能写成 ``agent_name`` / ``name``，
        内容可能写成 ``message`` / ``task``，这里都收。
        """
        target = (agent_name or name or "").strip()
        content = (message or task or "").strip()
        if not target:
            return self._diagnostic("缺少 agent_name；" + self._available_hint())
        if not content:
            return self._diagnostic("缺少 message；" + self._available_hint())

        runtime = self._resolve_runtime(target)
        if runtime is None:
            return self._diagnostic(f"未找到 agent {target!r}；" + self._available_hint())

        for attr in ("reply", "arun", "run", "handle_user_message", "__call__"):
            entry = getattr(runtime, attr, None)
            if not callable(entry):
                continue
            for args, kwargs in (
                ((content,), {}),
                ((), {"message": content}),
                ((), {"user_message": content}),
                ((), {"prompt": content}),
                ((content,), {"session_id": getattr(self.app_instance, "session_id", None)}),
            ):
                try:
                    produced = entry(*args, **kwargs)
                except TypeError:
                    continue
                if inspect.isawaitable(produced):
                    produced = await produced
                return self._as_text(produced)

        return self._diagnostic(
            f"agent {target!r} 没有可调用的委派入口；" + self._available_hint()
        )

    # --- 内部 -----------------------------------------------------------
    def _resolve_runtime(self, agent_name: str) -> typing.Any:
        runtimes = getattr(self.app_instance, "agent_runtimes", None)
        if isinstance(runtimes, typing.Mapping):  # type: ignore[arg-type]
            found = runtimes.get(agent_name)
            if found is not None:
                return found
        getter = getattr(self.app_instance, "get_agent_runtime", None)
        if callable(getter):
            try:
                return getter(agent_name)
            except Exception:  # noqa: BLE001 - 委派目标不存在是常态，走诊断文案
                return None
        return None

    def _available_hint(self) -> str:
        names = self.available_agent_names()
        return f"当前可委派：{', '.join(names)}" if names else "当前没有可委派的 agent"

    @staticmethod
    def _diagnostic(message: str) -> str:
        return f"[call_subagent] {message}"

    @staticmethod
    def _as_text(produced: typing.Any) -> str:
        if isinstance(produced, str):
            return produced
        for attr in ("content", "text", "message"):
            value = getattr(produced, attr, None)
            if isinstance(value, str):
                return value
        return str(produced)

    @staticmethod
    def _text_tuple(value: typing.Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,) if value.strip() else ()
        try:
            items = list(value)
        except TypeError:
            return ()
        result: list[str] = []
        for item in items:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
        return tuple(result)

    def __repr__(self) -> str:
        return f"ExpertCallService(agents={len(self.available_agent_names())})"
