"""Agent 实例的生命周期辅助（命名 / 元数据装配）。

``AgentFactory`` 持有本类的唯一实例（``_agent_instance_manager``），装配期被
``agent_runtime_composition`` 调用两次：

- ``determine_agent_name(...)``：定 agent 的最终名字（override > 声明名 > slot
  > 类型名）；
- ``configure_agent_metadata(...)``：把系统提示的来源/是否被优化过记到 agent 上。
- ``create_agent_instance(...)``：真正构造 agent 实例（无 plan 的分支走这里），
  并顺带把系统提示元数据写到产物上。

前两处**必须容错**：命名和元数据是辅助信息，绝不能因为它们把已经构造好的
agent 报废掉。所以拿不到就退化，而不是抛异常。

``create_agent_instance`` 相反：它是装配的主产物，构造失败必须向上抛，不能
悄悄返回 ``None``（否则调用方拿到 None 会在更远的地方崩）。它只做"入参整形"
这层容错——把工厂不认识的关键字过滤掉、把元数据关键字挑出来另走一路。
"""

from __future__ import annotations

import collections.abc as cabc
import typing
import inspect
import logging

import domains.agent.application.context.execution_store as agent_execution_store
from domains.plan.application.plan_runtime import PlanRuntime
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.agent.domain.entities.agent_context as agent_context_module
import domains.tool.core.application.toolkit as toolkit_module
import domains.agent.domain.aggregates.message_formatter as  message_formatter
import domains.agent.domain.value_objects.conversation_context as context_module

ReActAgent= agent_module.ReActAgent
logger = logging.getLogger(__name__)

__all__ = ["AgentInstanceManager"]


def _attr(source: object, *names: str, default: object = None) -> object:
    """按名字依次取属性/键，取不到返回 ``default``。"""
    if source is None:
        return default
    for name in names:
        if isinstance(source, cabc.Mapping):
            if name in source:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return default


class AgentInstanceManager:
    """确定 agent 名字、登记 agent 元数据、构造 agent 实例。

    无状态：所有需要的东西都从入参里拿，方便在装配期任意时刻调用。
    """

    # ------------------------------------------------------------------
    # 命名
    # ------------------------------------------------------------------

    def determine_agent_name(self, *args: object, **kwargs: object) -> str:
        """确定 agent 最终名字。

        调用点有两种形状，都要能接住：

        - ``determine_agent_name(host, sub_agent, override_name)``（位置参数）；
        - ``determine_agent_name(sub_agent=..., override_name=...)``（关键字）。

        优先级：``override_name`` → 声明名（``name`` / ``agent_name``）→
        slot 名 → 类型名。
        """
        sub_agent, override_name = self._split_args(args, kwargs)

        for candidate in (
            override_name,
            _attr(sub_agent, "name", "agent_name"),
            _attr(sub_agent, "agent_slot"),
        ):
            text = self._clean(candidate)
            if text:
                return text

        slot = _attr(sub_agent, "agent_slot")
        text = self._clean(_attr(slot, "name", "agent_name", "slot_name", default=None))
        if text:
            return text

        return type(sub_agent).__name__ if sub_agent is not None else "agent"

    def _split_args(
        self,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> tuple[object | None, object | None]:
        """从两种调用形状里还原 ``(sub_agent, override_name)``。"""
        override = kwargs.get("override_name")
        sub_agent = (
            kwargs.get("sub_agent")
            or kwargs.get("agent_runtime")
            or kwargs.get("runtime")
        )

        positional = list(args)
        # 第一个位置参数可能是宿主（host），它没有 agent 声明特征，跳过。
        while positional and self._looks_like_host(positional[0]):
            positional.pop(0)

        if sub_agent is None and positional:
            sub_agent = positional.pop(0)
        if override is None and positional:
            override = positional.pop(0)
        return sub_agent, override

    @staticmethod
    def _looks_like_host(value: object) -> bool:
        """宿主（``AgentFactory`` / app）带 ``config_manager``，agent 声明不带。"""
        return any(
            hasattr(value, attr)
            for attr in ("config_manager", "app_instance", "agent_factory")
        )

    @staticmethod
    def _clean(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        for attr in ("name", "agent_name", "slot_name"):
            inner = getattr(value, attr, None)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        text = str(value).strip()
        # 非字符串的兜底（枚举、slot 对象等）只在其确实可读时采用。
        return "" if text.startswith("<") else text

    # ------------------------------------------------------------------
    # 实例创建
    # ------------------------------------------------------------------

    # 指代工厂的关键字（不进构造函数）。
    _FACTORY_KEYS = ("agent_factory", "factory", "react_agent_factory")
    # 元数据关键字（不进构造函数，转交 configure_agent_metadata）。
    _METADATA_KEYS = (
        "original_system_prompt",
        "optimized_system_prompt",
        "optimization_applied",
    )

    def create_agent_instance(
        self,
        agent_name:str,
        system_prompt:str,
        model:chat_model.ChatModel,
        formatter: message_formatter.MessageFormatter,
        toolkit: toolkit_module.Toolkit,
        context: context_module.Context,
        max_iters: int |None=None,
        enable_meta_tool:bool=True,
        parallel_tool_calls: bool=True,
        agent_context:agent_context_module.AgentContext|None=None,
        tool_classification_resolver: runtime_ports.ToolClassificationResolverProtocol|None=None,
        model_id:str|None=None,
        model_profile_id:str|None=None,
        model_context_window_size:int|None=None,
        execution_store: agent_execution_store.AgentExecutionStore|None=None,
        scheduler_resolver: runtime_ports.AgentSchedulerResolverProtocol|None=None,
        hook_agent_type: str|None=None,
        hook_agent_source_path:str|None=None,
        # 以下为「6 个独立 agent 类降级为配置预设」后透传给 ReActAgent 的参数。
        # 原先 ``runtime_composition._instantiate_agent`` 已经传了这些关键字，
        # 但本方法签名里没有，会抛
        # ``TypeError: create_agent_instance() got an unexpected keyword argument``。
        tool_call_mode:str="native",
        reflection_max_iterations:int=3,
        reflection_prompts:dict[str,str]|None=None,
        plan_prompts:dict[str,str]|None=None,
        tool_call_listener:typing.Callable[[dict[str,typing.Any]],None]|None=None,
        preset_type:str="react",
    )->ReActAgent:
       agent = ReActAgent(
           name=agent_name,
           sys_prompt=system_prompt,
           model=model,
           formatter=formatter,
           toolkit=toolkit,
           context=context,
           max_iters=max_iters,
           plan_runtime= PlanRuntime(),
           enable_meta_tool=enable_meta_tool,
           parallel_tool_calls= parallel_tool_calls,
           agent_context=agent_context,
           tool_classification_resolver=tool_classification_resolver,
           model_id = model_id,
           model_profile_id= model_profile_id,
           model_context_window_size=model_context_window_size,
           execution_store= execution_store,
           scheduler_resolver=scheduler_resolver,
           hook_agent_type =hook_agent_type,
           hook_agent_source_path= hook_agent_source_path,
           tool_call_mode=tool_call_mode,
           reflection_max_iterations=reflection_max_iterations,
           reflection_prompts=reflection_prompts,
           plan_prompts=plan_prompts,
           tool_call_listener=tool_call_listener,
           preset_type=preset_type,
       )
       return agent

    def _resolve_agent_factory(
        self,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> object | None:
        """按 显式关键字 → 位置参数 → 延迟导入兜底 的顺序找工厂。"""
        for key in self._FACTORY_KEYS:
            candidate = kwargs.get(key)
            if callable(candidate):
                return candidate

        for value in args:
            if callable(value) and not self._looks_like_host(value):
                return value

        try:
            import domains.agent.domain.aggregates.react_agent as react_agent_module
        except Exception:  # noqa: BLE001 - 兜底路径，缺失时由调用方报错
            logger.debug("延迟导入 runtime.service.react_agent 失败", exc_info=True)
            return None
        factory = getattr(react_agent_module, "ReActAgent", None)
        return factory if callable(factory) else None

    @classmethod
    def _normalize_ctor_kwargs(cls, kwargs: dict[str, object]) -> dict[str, object]:
        """去掉工厂关键字，并把 ``agent_name`` 归一成 ``name``。"""
        normalized = {
            key: value for key, value in kwargs.items() if key not in cls._FACTORY_KEYS
        }
        if "name" not in normalized and "agent_name" in normalized:
            normalized["name"] = normalized.pop("agent_name")
        return normalized

    @classmethod
    def _split_metadata(
        cls,
        kwargs: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """把元数据关键字从构造参数里分离出来。"""
        payload: dict[str, object] = {}
        metadata: dict[str, object] = {}
        for key, value in kwargs.items():
            if key in cls._METADATA_KEYS:
                metadata[key] = value
            else:
                payload[key] = value
        return payload, metadata

    @staticmethod
    def _filter_supported_kwargs(
        agent_factory: object,
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        """按工厂签名过滤关键字；签名拿不到或带 ``**kwargs`` 时原样透传。"""
        try:
            signature = inspect.signature(agent_factory)
        except (TypeError, ValueError):
            return dict(kwargs)

        params = signature.parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return dict(kwargs)

        accepted = set(params)
        dropped = sorted(key for key in kwargs if key not in accepted)
        if dropped:
            logger.debug(
                "create_agent_instance: 工厂 %s 不接受 %s，已忽略",
                getattr(agent_factory, "__name__", agent_factory),
                dropped,
            )
        return {key: value for key, value in kwargs.items() if key in accepted}

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------

    def configure_agent_metadata(
        self,
        agent: object | None = None,
        *,
        original_system_prompt: str | None = None,
        optimized_system_prompt: str | None = None,
        optimization_applied: bool | None = None,
        **extra: object,
    ) -> None:
        """把系统提示的来源与优化状态写到 agent 上。

        同时写两处，兼容不同的读取方：

        - ``agent.prompt_metadata``：结构化字典；
        - ``agent._prompt_optimization_*``：若干扁平属性。

        agent 是 frozen 或 setter 受限时，只记 debug 日志——元数据写不进去
        不该让装配失败。
        """
        if agent is None:
            return

        applied = bool(optimization_applied) if optimization_applied is not None else (
            optimized_system_prompt is not None
            and original_system_prompt is not None
            and optimized_system_prompt != original_system_prompt
        )

        metadata: dict[str, object] = {
            "original_system_prompt": original_system_prompt,
            "optimized_system_prompt": optimized_system_prompt,
            "optimization_applied": applied,
        }
        metadata.update(extra)

        try:
            setattr(agent, "prompt_metadata", metadata)
            setattr(agent, "_prompt_optimization_applied", applied)
            setattr(agent, "_original_system_prompt", original_system_prompt)
            setattr(agent, "_optimized_system_prompt", optimized_system_prompt)
        except Exception:  # noqa: BLE001 - 元数据是可选能力
            logger.debug("failed to attach prompt metadata on %r", agent, exc_info=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "AgentInstanceManager()"
