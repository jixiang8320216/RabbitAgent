
from __future__ import annotations

import ast
import asyncio
import inspect
import typing
import collections
import logging
import datetime as datetime_module

import domains.agent.domain.value_objects.message as message
import domains.agent.domain.entities.agent_context as agent_context_module
import domains.agent.domain.aggregates.chat_model as chat_model
import domains.agent.domain.aggregates.message_formatter as message_formatter
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
import domains.agent.domain.value_objects.conversation_context as conversation_context
import domains.agent.domain.value_objects.agent_execution as agent_execution
import domains.agent.domain.ports.execution_store as agent_execution_store
import domains.agent.domain.aggregates.react_agent_reply as react_agent_reply
import domains.agent.domain.aggregates.react_agent_streaming as react_streaming
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.agent.domain.value_objects.runtime_context as runtime_context
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
import domains.hook.domain.outputs as hook_outputs
import domains.hook.domain.hook_definitions as hook_models
import domains.agent.domain.aggregates.tool_loop_detection as tool_loop_detection
import domains.runtime.application.hook_lifecycle as hook_lifecycle_module
from domains.agent.domain.entities import agent_transcript
from domains.observability.application import observability_context
import domains.agent.domain.aggregates.tool_invocation_scope as tool_invocation_scope
import domains.agent.domain.value_objects.tool_response as tool_response_model
from domains.observability.application.observability_context import increment_tool_call_count

get_app_instance = execution_context.get_app_instance

type HookContext = dict[str,object]
type HookResult= HookContext |None
type HookCallback= typing.Callable[..., HookResult| typing.Awaitable[HookResult]]
get_session_id= execution_context.get_session_id
get_project_home=execution_context.get_project_home
import domains.agent.domain.value_objects.chat_response as chat_response
import domains.hook.domain.inputs as hook_inputs


logger = logging.getLogger("react-agent")



_DEFAULT_PLANNER_PROMPTS:dict[str,str]={
    "planner":(
"""
你是一个顶级的AI规划专家。 你的任务是将用户提出的复杂问题分解成一个由多个简单步骤组成的行动计划。
请确认计划中的每个步骤都是一个独立的/可执行的子任务，并且严格按照逻辑顺序排列。
你的输出必须是一个Python列表， 其中每个元素都是一个描述子任务的字符串。

问题： {question}
请严格按照以下格式输出你的计划
'''python
["步骤1","步骤2","步骤3"，...]
'''
"""
    ),
    "executor":(
"""
你是一位顶级的AI执行专家。你的任务时严格按照给定的计划，一步步地解决问题。
你将收到原始问题、完整的计划、以及到目前为止已经完成的步骤和结果。
请你专注与解决"当前步骤"， 并仅输出该步骤的最终答案，不要输出任何额外的解释或对话。

# 原始问题:
{question}

# 完整计划
{plan}

# 历史步骤与结果
{history}

# 当前步骤
{current_step}

请仅输出针对"当前步骤"的回答
"""
    )
}

_DEFAULT_REFLECTION_PROMPTS:dict[str,str]={
    "initial": ("""
    请根据以下要求完成任务：
    任务:{task}  
    请提供一个完整/准确的回答。  
    """),
    "reflect": ("""
    请仔细审查以下答案， 并找出可能的问题或改进空间：
    # 原始任务
    {task}
    # 当前回答
    {content}
    请分析这个回答的质量，指出不足之处，并提出具体的改进建议。
    如果回答已经很好，请回答"无需改进"。
    """),
    "refine": ("""
    请根据反馈意见改进你的回答：
    # 原始任务：
    {task}
    # 上一轮回答：
    {last_attempt}
    # 反馈
    {feedback}
    请提供一个改进后的回答。
    """)
}

@typing.runtime_checkable
class _ToolLoopConfigurationPort(typing.Protocol):
    tool_loop_detection_enabled: bool
    tool_loop_repeat_threshold: int
    tool_loop_window_size:int
    tool_loop_max_reminders_per_run:int

@typing.runtime_checkable
class _ToolLoopApplicationPort(typing.Protocol):
    config: _ToolLoopConfigurationPort

@typing.runtime_checkable
class  _HookLifecycleApplicationPort(typing.Protocol):
    @property
    def hook_lifecycle(self)->hook_lifecycle_module.HookLifecycleCoordinator|None:...

    def build_hook_lifecycle_metadata(self,
        session_id:str,
        *
        correlation_id:str,
        prompt_id:str|None=None,
        entry_agent:ReActAgent|None=None,
        model_index:int|None=None,
        agent_id:str |None=None,
        agent_type:str |None=None,
    )-> hook_lifecycle_module.HookLifecycleMetadata:...

class ReActAgent:
    HOOK_TYPES=[
        "pre_reply",
        "post_reply",
        "pre_reasoning",
        "post_reasoning",
        "model_error",
        "pre_acting",
        "post_acting",
        "pre_print",
        "post_print",
        "pre_observe",
        "post_observe",
    ]


    def __init__(
        self,
        name:str,
        sys_prompt: str,
        model: chat_model.ChatModel,
        formatter: message_formatter.MessageFormatter,
        toolkit: runtime_ports.ToolkitProtocol,
        context: conversation_context.Context,
        max_iters: int | None=None,
        plan_runtime: runtime_ports.PlanningRuntimeProtocol |None=None,
        parallel_tool_calls: bool=False,
        enable_meta_tool: bool=False,
        agent_context: typing.Optional[agent_context_module.AgentContext]= None,
        tool_classification_resolver: runtime_ports.ToolClassificationResolverProtocol |None=None,
        model_id: str |None=None,
        model_profile_id: str |None=None,
        model_context_window_size:int |None=None,
        prompt_provenance: agent_execution.AgentPromptProvenance|None=None,
        execution_store: agent_execution_store.AgentExecutionStore |None=None,
        scheduler_resolver :runtime_ports.AgentSchedulerResolverProtocol |None=None,
        hook_agent_type:str |None=None,
        hook_agent_source_path:str |None=None,
        tool_call_mode:typing.Literal["native","text_parse"]="native",
        reflection_max_iterations: int=3,
        reflection_prompts: dict[str,str]|None=None,
        plan_prompts:dict[str,str]|None=None,
        tool_call_listener: typing.Callable[[dict[str, typing.Any]], None]|None=None,
        preset_type:str="react",
    )->None:
        self.name = name
        self._sys_prompt = sys_prompt
        self.model = model
        self.formatter = formatter
        self.toolkit = toolkit
        self.context = context
        self.max_iters = max_iters
        self.plan_runtime = plan_runtime
        self.parallel_tool_calls = parallel_tool_calls
        self.enable_meta_tool = enable_meta_tool

        self.agent_context:typing.Optional[agent_context_module.AgentContext]= agent_context
        self.tool_classification_resolver = tool_classification_resolver
        self.model_id = model_id
        self.model_profile_id = model_profile_id
        self.model_context_window_size = model_context_window_size
        self.prompt_provenance = prompt_provenance
        self.tool_telemetry_emitter : runtime_ports.ToolTelemetryEmitterProtocol |None=None
        self.tool_observability_metadata_resolver: runtime_ports.ToolObservabilityMetadataResolverProtocol |None=None
        self.tool_args_summary_resolver: runtime_ports.ToolArgsSummaryResolverProtocol |None=None
        self._execution_store = execution_store
        self._context_persistence: runtime_ports.AgentContextPersistenceProtocol |None=None
        self._scheduler_resolver = scheduler_resolver
        self.hook_agent_type = hook_agent_type
        self.hook_agent_source = hook_agent_source_path

        self._last_response_text:typing.Optional[str] = None

        self._last_run_iterations:int = 0
        self._tool_registry_service: object |None=None
        self._rabbit_task: typing.Optional[asyncio.Task]=None
        self._fallback_agent_tasks: set[asyncio.Task[None]]=set()
        self._hooks: dict[str,collections.OrderedDict[str,HookCallback]]={
            ht:collections.OrderedDict() for  ht in self.HOOK_TYPES
        }
        if self.name != "btw_agent" and self.agent_context is None:
            raise AssertionError(
                f"ReActAgent '{self.name}' constructed without agent_context;"
            )
        self.tool_call_mode = tool_call_mode
        self.reflection_max_iterations= reflection_max_iterations
        self.reflection_prompts = reflection_prompts or _DEFAULT_REFLECTION_PROMPTS
        # 原写成 ``self.plan_runtime = plan_prompts or _DEFAULT_PLANNER_PROMPTS``：
        # 把第 191 行注入的真实 ``PlanRuntime`` 覆盖成了一个 prompts 字典，
        # 于是 ``_reply_impl`` 里 ``plan_runtime is None`` 永远为假、plan_solve
        # 永不触发；且 ``self.plan_prompts`` 从未被赋值，一旦走到
        # ``_reply_plan_solve`` 就是 ``AttributeError``。这里改为正确字段。
        self.plan_prompts = plan_prompts or _DEFAULT_PLANNER_PROMPTS
        self.tool_call_listener = tool_call_listener
        # ``preset_type``：6 个独立 agent 类（SimpleAgent / FunctionCallAgent /
        # PlanAndSolveAgent / ReflectionAgent / ToolAwareSimpleAgent / ReActAgent）
        # 降级后的配置预设值，由 ``runtime_composition._instantiate_agent`` 从
        # ``AgentRuntime.preset_type`` 透传而来，决定走哪条回复策略
        # （见 ``_reply_impl`` / ``_resolve_reply_strategy``）。
        self.preset_type = preset_type or "react"
        # 记录「是否显式配置」，供旧启发式 fallback 判断该不该生效。
        self._reflection_explicitly_configured = reflection_prompts is not None
        self._plan_prompts_explicitly_configured = plan_prompts is not None

    def bind_tool_telemetry_emitter(self, emitter: runtime_ports.ToolTelemetryEmitterProtocol)->None:
        self.tool_telemetry_emitter = emitter

    def bind_tool_observability_metadata_resolver(self, resolver: runtime_ports.ToolObservabilityMetadataResolverProtocol)->None:
        self.tool_observability_metadata_resolver = resolver

    def bind_tool_args_summary_resolver(self,
        resolver: runtime_ports.ToolArgsSummaryResolverProtocol)->None:
        self.tool_args_summary_resolver = resolver

    def bind_context_persistence(self, persistence: typing.Any|None)->None:
        """挂上上下文持久化回调（``persist_agent_context_strict``）。

        ``agent_runtime_composition._finalize_agent`` 在装配期按这个名字调用；
        ``__init__`` 里只留了 ``_context_persistence`` 字段而没有这个方法，
        装配会直接 ``AttributeError``。传 ``None`` 表示不持久化。
        """
        self._context_persistence = persistence

    async def persist_context_memory_strict(
        self,
        expected_message_ids: collections.abc.Collection[str],
    ):
        expected_id_sequence = tuple(expected_message_ids)
        expected_ids = frozenset(expected_id_sequence)
        if not expected_ids or len(expected_ids) !=len(expected_id_sequence):
            raise ValueError("strcit Content persistence requires disctinct")
        live_ids = frozenset(runtime_message.id for runtime_message in self.context.context)
        if not expected_ids.issubset(live_ids):
            raise ValueError("strcit Content persistence requires disctinct")
        persistence = self._context_persistence
        if persistence is None:
            return
        state = conversation_context.ContextState.model_validate(self.context.state_dict())
        await persistence(self.name, state)

    async def _dispatch_post_tool_batch(self,
        tool_results: list[tuple[message.ToolUseBlock, tool_response_model.ToolResponse]],
    )->None:
        if not tool_results:
            return
        try:
            app=get_app_instance()
            session_id = get_session_id()
            if not isinstance(app, _HookLifecycleApplicationPort) or not session_id:
                return
            lifecycle = app.hook_lifecycle
            if lifecycle is None:
                return
            metadata = app.build_hook_lifecycle_metadata(
                session_id=session_id,
                correlation_id =(
                    observability_context.get_trace_id() or f"tool-batch;{tool_results[0][0].get('id','unknown')}"
                ),
                entry_agent=self,
                agent_id = self.name,
                agent_type =(
                    self.hook_agent_type
                    or ("root" if self.name == execution_context.get_root_agent_id() else "subagent")
                ),
            )
            calls= tuple(
                hook_inputs.ToolBatchCall(
                    tool_name= tool_use.name,
                    tool_input= tool_use.input,
                    tool_use_id = tool_use.id,
                    tool_response= tool_invocation_scope.ToolInvocationScope.response_json(response)
                )
                for tool_use, response in tool_results
            )
            result = await lifecycle.post_tool_batch(metadata,calls)
            decision = result.decision
            feedback = [*decision.additional_context, * decision.system_messages]
            if not decision.continue_processing or decision.action.value in {"block","deny"}:
                feedback.extend(decision.reason
                                or decision.stop_reason
                                or "Configuration Hook blocked processing after the tool batch")
            if feedback:
                response = tool_results[-1][1]
                reminder ="<system-reminder>\n"+"\n\n".join(feedback) +"\n</system-reminder>"
                response.content.append(message.TextBlock(type="text", text=reminder))
        except Exception as e:
            logger.error(e)

    async def _call_model_with_error_recovery(
         self,
         formatted:list[chat_model.JsonObject],
         tool_schema:list[chat_model.JsonObject],
          *,
          iteration:int,
    )->typing.AsyncGenerator[chat_response.ChatResponse,None]:
        max_retries =2
        retry_count = 0
        while True:
            try:
                return await self.model(formatted, tools=tool_schema or None)
            except Exception as e:
                hook_ctx= await self._fire_hooks(
                    "model_error",
                    {
                        "error":e,
                        "iteration":iteration,
                        "agent_name":self.name,
                        "retry_allowed":retry_count<max_retries,
                        "retry_count":retry_count,
                        "formatted":formatted,
                        "tool_schema":tool_schema,
                    }
                )
                if hook_ctx.get("retry") and retry_count < max_retries:
                    retry_count += 1
                    sys_msg = message.Msg("system",self._sys_prompt, "system")
                    memory_msgs= await self.context.get_messages()
                    formatted= await self.formatter.format(msgs=[sys_msg,*memory_msgs])
                    tool_schema= self.toolkit.get_json_schemas() if self.toolkit else []
                    continue
                raise

    async def reply(self, msg: message.Msg)->message.Msg:

        await self._fire_hooks("pre_reply",{"msg":msg,"agent_name": self.name})
        response_msg: typing.Optional[message.Msg]=None
        reply_exception: typing.Optional[BaseException]=None

        try:
            response_msg = await self._reply_impl(msg)
            return response_msg
        except BaseException as e:
            try:
                await self._fire_hooks(
                    "model_error",
                    {
                        "msg": msg,
                        "agent_name": self.name,
                        "error":e,
                    },
                )
            except Exception as hook_err:
                logger.error(hook_err)
            raise
        finally:
            try:
                await self._fire_hooks(
                    "post_reply",
                    {
                        "msg":msg,
                        "agent_name":self.name,
                    },
                )
            except Exception as hook_err:
                logger.error(hook_err)
            self._notify_iteration_count_lifecycle()

    def _update_registry_status(
        self,
        status: agent_context_module.AgentStatus,
        *,
        transition:str,
    )->None:
        try:
            context= self.agent_context
            if context is not None and context.registry is not None:
                context.registry.update_status(context.agent_id, status)
        except Exception as exc:
            logger.error(exc)

    def _mark_interrupted(self)->None:
        try:
            context= self.agent_context
            if context is None:
                return
            context.status = agent_context_module.AgentStatus.INTERRUPTED
            self._persist_state()
            registry = getattr(context, "registry", None)
            if registry is not None:
                agent_id = getattr(context, "agent_id", None)
                registry.update_status(
                    agent_id,
                    agent_context_module.AgentStatus.INTERRUPTED,
                )
        except Exception as exc:
            logger.error(exc)


    async def _run_registered_reply(self, msg: message.Msg) -> message.Msg:
        try:
            reponse = await self.reply(msg)
            self._update_registry_status(
                agent_context_module.AgentStatus.COMPLETED,
                transition="Completion"
            )
            return reponse
        except asyncio.CancelledError:
            self._mark_interrupted()
        except Exception:
           self._update_registry_status(
               agent_context_module.AgentStatus.FAILED,
               transition="Failure"
           )
           raise

    async def _emit_tool_call_streaming(
         self,
         tool_id:str,
         tool_name:str,
         input_stream_phase:str,
         input_preview:str,
         input_fragment:str|None,
         input_fragment_index:int|None,
         input_fragment_kind:str|None,
    )->None:
        try:
            await self.model.observer.on_tool_call_streaming(
                self.name,
                tool_name=tool_name,
                tool_id=tool_id,
                operation_id=self.name,
                input_preview=input_preview[:2000],
                input_stream_phase=input_stream_phase,
                input_fragment=input_fragment,
                input_fragment_kind=input_fragment_kind,
                input_fragment_index = input_fragment_index,
            )
        except Exception as exc:
            logger.error(exc)

    async def __call__(self, msg: message.Msg)->message.Msg:
        self._reply_task = asyncio.current_task()
        self._register_execution_context()
        try:
            return await self._run_registered_reply(msg)
        finally:
            self._reply_task = None

    async def _emit_text_delta(self, delta:str)->None:
        try:
            await self.model.observer.on_text_delta(self.name, delta, operation_id=None)
        except Exception as exc:
            logger.error(exc)

    # ``preset_type``（6 个独立 agent 类降级后的配置预设值）-> 回复策略。
    # 这是「多类继承」改为「单类 + 配置预设」后唯一的路由表。
    _PRESET_REPLY_STRATEGY: dict[str, str] = {
        "react": "react",           # 原生 ReAct 主循环
        "function_call": "react",   # 原 FunctionCallAgent：native 工具调用
        "tool_aware": "react",      # 原 ToolAwareSimpleAgent：ReAct + 工具监听
        "simple": "text_parse",     # 原 SimpleAgent：无 native 工具，文本标记解析
        "plan_solve": "plan_solve", # 原 PlanAndSolveAgent：规划-执行
        "reflection": "reflection", # 原 ReflectionAgent：反思迭代
    }
    # 共用 ReAct 主循环、但仍可被 ``tool_call_mode`` 单独切成文本解析的预设集
    _REACT_FAMILY_PRESETS: frozenset[str] = frozenset(
        {"react", "function_call", "tool_aware"}
    )

    def _resolve_reply_strategy(self, msg: message.Msg) -> str:
        """决定本次回复走哪条策略实现。

        优先级：**显式 ``preset_type``** → **``tool_call_mode``** → **旧启发式**。
        旧的启发式（``max_iters < 0`` / ``msg.content.startswith('__plan_solve__')``）
        保留下来只为兼容尚未升级的配置，不再是唯一入口。
        """
        preset = (self.preset_type or "react").strip().lower()
        strategy = self._PRESET_REPLY_STRATEGY.get(preset)
        if strategy is None:
            logger.warning(
                "未知的 preset_type=%r，回退到 react 主循环", self.preset_type,
            )
            preset, strategy = "react", "react"

        if preset in self._REACT_FAMILY_PRESETS:
            # ``_reply_text_parse`` 此前是死代码，没有任何入口调用它；
            # 这里让 tool_call_mode='text_parse' 成为它的正式入口。
            if self.tool_call_mode == "text_parse":
                return "text_parse"
            # ---- 旧启发式 fallback（向后兼容） ----
            if (
                self._reflection_explicitly_configured
                and self.max_iters is not None
                and self.max_iters < 0
            ):
                return "reflection"
            content = getattr(msg, "content", None)
            if (
                self.plan_runtime is None
                and self._plan_prompts_explicitly_configured
                and isinstance(content, str)
                and content.startswith("__plan_solve__")
            ):
                return "plan_solve"
            return "react"
        return strategy

    async def _reply_impl(self, msg: message.Msg)->message.Msg:
        """按 ``preset_type`` 分流到具体的回复策略实现。

        架构约定：``reply`` 只保留 ``pre_reply`` / ``model_error`` / ``post_reply``
        的生命周期外壳，真正的策略主体全部下沉到这里 —— 即「用 ``_reply_*`` 的
        内部实现替换 ``reply`` 的内部实现」。新增一种 agent 行为只需加一个
        ``_reply_xxx`` 方法 + 在 ``_PRESET_REPLY_STRATEGY`` 登记，无需新建类。
        """
        strategy = self._resolve_reply_strategy(msg)
        if strategy == "reflection":
            return await self._reply_reflection(msg)
        if strategy == "plan_solve":
            return await self._reply_plan_solve(msg)
        if strategy == "text_parse":
            return await self._reply_text_parse(msg)
        # 默认：ReAct 主循环（native / text_parse 两种工具调用模式）
        return await  react_agent_reply.run_react_reply(self, msg,
                                                        app_resolver= self._resolve_app_instance,
                                                        session_id_resolver= self._resolve_session_id,
                                                        event_logger=logger)

    # ------------------------------------------------------------------
    # 生命周期：hook 分发 / 执行上下文登记 / 迭代计数 / 状态持久化
    #
    # 这一层的四个方法都是「**有消费者就报出去，没有就静默降级**」：
    # ``agent.model.agent_context``、``agent.service.agent_execution_store``
    # 目前都还是空模块，对应的类根本不存在。用 ``hasattr`` / ``getattr`` 探测，
    # 避免把这些缺口变成启动期或运行期的 AttributeError。
    # ------------------------------------------------------------------

    async def _fire_hooks(
        self,
        event: str,
        kwargs:HookContext
    ) ->HookContext:
        # HOOK_TYPES 之外也有 hook（pre_reply / post_reply 等），且桶可能从未创建；
        # 直接下标会在第一轮就 KeyError，把整轮对话炸掉。
        bucket = self._hooks.get(event)
        if not bucket:
            return kwargs
        for _hook_name, hook_func in bucket.items():
            try:
                sig = inspect.signature(hook_func)
                nparams= len(sig.parameters)
            except (TypeError, ValueError):
                nparams = 2

            if nparams >=3:
                call_args=(self, kwargs, kwargs.get("output"))
            else:
                call_args = (self, kwargs)

            result = hook_func(*call_args)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                kwargs=result

        return kwargs

    def register_instance_hook(self,event:str, name:str, callback: HookCallback)->None:
        if event not in self._hooks:
            raise ValueError(f"Event {event} not registered")
        self._hooks[event][name] = callback

    def take_last_response_text(self)->str|None:
        response_text = self._last_response_text
        self._last_response_text = None
        return response_text


    def _register_execution_context(self) -> None:
        """把本次调用的运行时信息登记到上下文，供 tool / plugin / observer 回溯。

        分三步，任一步缺失都只降级、不抛：

        1. **当前 task**：记到 ``_rabbit_task``，中断逻辑靠它取消这一轮；
        2. **执行上下文接口**：``runtime.service.execution_context`` 一旦补齐
           ``set_root_agent_id`` / ``set_current_agent`` / ``register_agent``
           之类的登记接口就顺手写进去（当前模块里还没有这些函数）；
        3. **agent_context 回填**：``agent_context`` 上本来就有同名字段时
           （``task`` / ``rabbit_task`` / ``last_run_iterations`` 等）才赋值，
           不凭空给可能冻结的模型加属性。

        注意不能反向调用 ``bind_execution_context`` 覆盖 app 级上下文——那是
        ``app_id`` / ``project_home`` 级别的绑定，这里是 agent 级的。
        """

        try:
            context= self.agent_context
            if context is not None and context.registry is not None:
                context.registry.register(context)
        except Exception as exc:  # noqa: BLE001 - 登记失败不该打断调用
            logger.debug(
                "[REACT-AGENT] agent %r cannot register into execution context: %s",
                self.name,
                exc,
            )


    def _notify_iteration_count_lifecycle(self) -> None:
        """一轮结束后把**迭代轮数**报给关心它的观测方。

        轮数由编排器回写到 ``self._last_run_iterations``（见
        ``runtime.service.react_agent_reply``）。现在的消费者基本都还没实现，
        所以逐个探测：

        - ``agent_context`` 上已有的同名字段（``iteration_count`` /
          ``last_run_iterations`` / ``iterations``）——只改已存在的字段；
        - ``_execution_store`` 的记录接口（``record_iteration_count`` /
          ``record_iterations`` / ``update_iterations`` / ``note_iterations``）；
        - 应用侧的执行通知器（``lifecycle_notifier.notify_*``）。

        **只做同步调用**：本方法跑在 ``reply()`` 的 ``finally`` 里，返回协程没有
        人会 await，反而会留下 "coroutine was never awaited" 警告。
        """
        iterations= self._last_run_iterations
        if iterations<=0:
            return
        notifier= runtime_bindings.get_lifecycle_notifier()
        if notifier is None:
            return
        try:
            session_id =get_session_id()
            project_home = get_project_home()
            if not project_home and not session_id:
                ctx=runtime_context.get_runtime_context_or_none()
                if ctx is not None:
                    default_workplace = ctx.default_project_home
                    if default_workplace is not None:
                        project_home = str(default_workplace)
            # 本方法文档已说明"消费者基本都还没实现，所以逐个探测"，但这一处漏了探测：
            # HarnessLifecycleNotifier 没有 notify_iteration_count，直接调用会抛
            # "'HarnessLifecycleNotifier' object has no attribute
            # 'notify_iteration_count'"。按同一模式补上能力探测。
            notify_iterations = getattr(notifier, "notify_iteration_count", None)
            if notify_iterations is not None:
                notify_iterations(
                    session_id=session_id,
                    iterations=iterations,
                    project_home=project_home,
                    evidence={"source":"react_agent.reply","agent_name":self.name},
                )
        except Exception as exc:
            logger.error(exc)

    def _persist_state(self) -> None:
        if not self.agent_context or self._execution_store is None:
            return
        try:
            ctx=self.agent_context
            ctx.last_heartbeat= datetime_module.datetime.now().isoformat()
            execution_store =self._execution_store
            execution_store.save_state(ctx)
            # ``FileAgentExecutionStore`` 目前只实现了 ``paths()`` / ``save_state()``，
            # 没有 ``save_memory()``。原代码无条件调用会抛
            # "'FileAgentExecutionStore' object has no attribute 'save_memory'"，
            # 而它紧跟在 save_state 之后——异常一抛，心跳虽然写进了内存对象，
            # 但后续对同一个 try块的任何操作都会被跳过。这里做能力探测。
            saver = getattr(execution_store, "save_memory", None)
            if saver is not None and self.context:
                saver(ctx, self.context.state_dict())
        except Exception as exc:
            logger.exception("persist_state failed: %s", exc)

    # ------------------------------------------------------------------
    # 编排器回调：应用实例 / 会话标识 / 工具环检测 / 收尾摘要
    #
    # ``react_agent_reply.ReactReplyOrchestrator.run`` 每轮都会调
    # ``_create_tool_loop_detector``，迭代耗尽时调 ``_summarize``；
    # ``run_react_reply`` 则把两个 resolver 存起来，给 hook payload 和中断检查用。
    # 这四个都是主循环的必需入口，缺一个整轮就 ``AttributeError``。
    # ------------------------------------------------------------------

    def _resolve_app_instance(self) -> object | None:
        """取回当前应用实例；取不到返回 ``None``。

        编排器只把它塞进 hook payload 供插件使用，拿不到不影响本轮推理——所以
        这里吞掉一切异常，绝不让它把一轮对话打断。
        """
        getter = getattr(execution_context, "get_app_instance", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[REACT-AGENT] agent %r resolve app instance failed: %s", self.name, exc
            )
            return None

    def _resolve_session_id(self) -> str | None:
        """取回当前会话标识；取不到返回 ``None``。

        用于中断判定的 scope key 与日志关联字段。
        """
        try:
            return get_session_id()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[REACT-AGENT] agent %r resolve session id failed: %s", self.name, exc
            )
            return None

    def _create_tool_loop_detector(self) -> tool_loop_detection.ToolLoopDetector | None:
        """创建本轮的工具环检测器。

        检测器形状尚未定型（可能按 ``(name, arguments)`` 记账，也可能直接看消息
        序列），编排器那边靠签名探测兼容。这里返回 ``None`` 表示「本轮不检测」，
        编排器会当成「未成环」继续跑——工具环只会导致多跑几轮，误判成环则会
        提前掐掉正常的多步任务，后者更糟。
        """
        try:
            app=get_app_instance()
            if app is None or not isinstance(app,_ToolLoopApplicationPort):
                return tool_loop_detection.ToolLoopDetector()
            config = app.config
            if not isinstance(config,_ToolLoopConfigurationPort):
                return tool_loop_detection.ToolLoopDetector()
            if not config.tool_loop_detection_enabled:
                return None
            return tool_loop_detection.ToolLoopDetector(
                threshold=config.tool_loop_repeat_threshold,
                window_size=config.tool_loop_window_size,
                max_reminders=config.tool_loop_max_reminders_per_run,
            )
        except Exception as exc:
            logger.error(exc)
        return None

    async def _summarize(self) -> message.Msg:
        """迭代耗尽时的收尾回复。

        主循环退出有两条路：模型给出终局回复，或 ``max_iters`` 用完。走后者时
        上下文里已经有完整的推理/工具记录，这里挑最合适的一段文本收尾，而不是
        让用户面对一个空回复。

        优先级：``_last_response_text`` → 上下文里最后一条助手文本 → 最后一条
        工具结果 → 兜底提示。
        """
        text = self._last_response_text
        if not (isinstance(text, str) and text.strip()):
            text = self._last_assistant_text()
        if not text:
            text = self._last_tool_result_text()
        if not text:
            text = (
                "已完成本轮处理，但未生成最终结论"
                f"（达到迭代上限 {self.max_iters}）。"
            )
        self._last_response_text = text
        return message.Msg(self.name, text, "assistant")

    def _last_assistant_text(self) -> str:
        """从上下文里取最后一条非空的助手文本；取不到返回 ``''``。"""
        for item in reversed(self._history_items()):
            if getattr(item, "role", None) != "assistant":
                continue
            content = getattr(item, "content", None)
            candidate = content if isinstance(content, str) else self._blocks_text(content)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def _last_tool_result_text(self) -> str:
        """从上下文里取最后一条工具结果文本；取不到返回 ``''``。"""
        for item in reversed(self._history_items()):
            content = getattr(item, "content", None)
            if not isinstance(content, (list, tuple)):
                continue
            for block in reversed(content):
                if getattr(block, "type", None) not in ("tool_result", "tool-output"):
                    continue
                output = getattr(block, "output", None)
                if isinstance(output, str) and output.strip():
                    return output.strip()
        return ""

    @staticmethod
    def _blocks_text(content: object) -> str:
        """把内容块列表里的文本块拼成一段；非列表或没有文本块返回 ``''``。"""
        if not isinstance(content, (list, tuple)):
            return ""
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                continue
            if getattr(block, "type", None) == "text":
                value = getattr(block, "text", None)
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts).strip()

    def _history_items(self) -> list[object]:
        """取上下文历史；上下文形状不定（可迭代 / ``state_dict`` / 私有列表）。"""
        context = self.context
        if context is None:
            return []
        if isinstance(context, (list, tuple)):
            return list(context)
        for name in ("history", "messages", "items", "_messages"):
            value = getattr(context, name, None)
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
            if isinstance(value, (list, tuple)):
                return list(value)
        try:
            return list(context)
        except TypeError:
            return []

    async def _run_subagent_start_hook(
            self,
            *,
            spawn_id: str,
            agent_type: str,
    ) -> tuple[str,...]:
        """子 agent 起跑前分发 ``subagent_start`` 钩子。

        语义上对应「父 agent 即将把一个子任务交给某个子 agent」这一刻：
        编排器在真正创建 / 调度子 agent **之前**调用它，插件可以在这里做
        准入判断、打点或改写传入子 agent 的上下文。

        与 :meth:`reply` 里的钩子不同，这里的返回值目前**只回给调用方**，
        不参与子 agent 的构造参数——子 agent 的装配参数由编排器自己持有，
        让钩子改写它需要在编排器和本类之间约定一份可变协议，代价大于收益。

        钩子抛异常只记日志、不影响子 agent 起跑：钩子是旁路的观察/装饰，
        它自己失败不该让主任务跟着失败。
        """
        lifecycle,metadata,_session_id = self._subagent_hook_dispatch_context(
            spawn_id=spawn_id,
            agent_type=agent_type,
            correlation_suffix ="start",

        )
        if lifecycle is None or metadata is None:
            return ()
        try:
            result = await lifecycle.subagent_start(
                metadata=metadata,
                agent_id=spawn_id,
                agent_type=agent_type,
            )
            decision=result.decision
            feedback = [*decision.additional_context,*decision.system_messages]
            if not decision.continue_processing or decision.action in {
                hook_outputs.HookDecisionAction.BLOCK,
                hook_outputs.HookDecisionAction.DENY,
            }:
                feedback.append(decision.reason or decision.stop_reason or "A configuration Hook reported a Subagent warning")
            return tuple(feedback)
        except Exception as exc:
            logger.error(exc)
            return ()

    def _get_execution_store(self)-> agent_execution_store.AgentExecutionStore|None:
        return self._execution_store

    async def _run_subagent_stop_hook(
            self,
            *,
            spawn_id: str,
            agent_type: str,
            last_assistant_message:str,
            stop_hook_active:bool,
    ) -> tuple[bool,tuple[str,...]]:
        """子 agent 收尾时分发 ``subagent_stop`` 钩子。

        无论子 agent 是正常跑完、被取消还是抛异常，编排器都要在收尾处调用
        一次，因此这里显式区分三种结局并写进上下文：

        - ``success=True`` / ``output`` —— 正常返回；
        - ``success=False`` / ``error`` —— 抛异常或被取消。

        放在 ``finally`` 里调用时建议把 ``error`` 传进来，否则插件只能看到
        一个「没有输出的结束」，无法区分成功与失败。

        同 :meth:`_run_subagent_start_hook`，钩子自身抛异常只记日志。
        """
        lifecycle,metadata, session_id = self._subagent_hook_dispatch_context(
            spawn_id=spawn_id,
            agent_type=agent_type,
            correlation_suffix ="stop",
        )
        if lifecycle is None or metadata is None or not session_id:
            return False,()
        try:
           parent_context = self.agent_context
           registry = parent_context.registry if parent_context is not None else None
           context = registry.get(spawn_id) if registry is not None else None
           execution_store = self._get_execution_store()
           if execution_store is None:
               return False,()
           if context is None:
               context= agent_context_module.AgentContext(
                   agent_id=spawn_id,
                   session_id=session_id,
               )
           transcript_path=execution_store.paths(context).transcript_path
           result = await lifecycle.subagent_stop(
               metadata=metadata,
               agent_id=spawn_id,
               agent_type=agent_type,
               agent_transcript_path=transcript_path,
               last_assistant_message=last_assistant_message,
               stop_hook_active=stop_hook_active,
           )
           decision=result.decision
           claim  = await lifecycle.claim_stop_continuation(
               session_id,
               hook_models.HookEvent.SUBAGENT_STOP,
               decision,
               agent_id=spawn_id,
           )
           if not claim.allowed:
               return False,()
           feedback = list(decision.additional_context)
           if decision.reason:
               feedback.append(decision.reason)
           if decision.stop_reason:
                feedback.append(decision.stop_reason)
           if not feedback:
                feedback.append("A configuration Hook reported a Subagent warning")
           return True, tuple(feedback)
        except Exception as exc:
            logger.error(exc)
            return False,()

    def _append_transcript_message(
         self,
         runtime_message:message.Msg,
        *,
        iteration:int,
    )->None:
        context =self.agent_context
        execution_store = self._execution_store
        if context is None or execution_store is None:
            return
        # FileAgentExecutionStore 目前只提供 paths()/save_state()，尚未实现
        # transcript 记录方法。原调用会抛
        # "'FileAgentExecutionStore' object has no attribute
        # 'append_transcript_message'"。这里做能力探测：store 未实现该能力时
        # 静默跳过（transcript 属旁路观测，不应影响主流程）。
        appender = getattr(execution_store, "append_transcript_message", None)
        if appender is None:
            return
        try:
            appender(
                context,
                runtime_message,
                agent_transcript.AgentTranscriptMetadata(iteration=iteration),
            )
        except Exception as exc:
            logger.error(exc)


    def _append_transcript_event(self, event: agent_transcript.AgentTranscriptEvent) -> None:
        context = self.agent_context
        execution_store = self._execution_store
        if context is None or execution_store is None:
            return
        # 同 _append_transcript_message：FileAgentExecutionStore 尚未实现
        # transcript 记录接口，未实现时安静跳过。
        appender = getattr(execution_store, "append_transcript_event", None)
        if appender is None:
            return
        try:
            appender(context, event)
        except Exception as exc:
            logger.error(exc)

    def _subagent_hook_dispatch_context(
            self,
            *,
            spawn_id:str,
            agent_type:str,
            correlation_suffix:str = "",
    )-> tuple[hook_lifecycle_module.HookLifecycleCoordinator|None, hook_lifecycle_module.HookLifecycleMetadata|None, str|None]:
        session_id: str|None = None
        try:
            app=get_app_instance()
            session_id = get_session_id()
            if not isinstance(app, _HookLifecycleApplicationPort) or not session_id:
                 return None, None, session_id
            lifecycle = app.hook_lifecycle
            if lifecycle is None:
                return None, None, session_id
            correlation_id = observability_context.get_trace_id() or f"subagent: {spawn_id}:{correlation_suffix}"
            metadata=app.build_hook_lifecycle_metadata(
                session_id,
                correlation_id=correlation_id,
                entry_agent=self,
                agent_id=spawn_id,
                agent_type=agent_type,
            )
            return lifecycle, metadata, session_id
        except Exception as exc:
            logger.error(exc)
            return None, None, session_id


    def _parse_text_tool_calls(self,text:str)->list[dict[str, str]]:
        """文本标记解析模式： 从自然语言中提取[TOOL_CALL: name: params] 标记
        原SimpleAgent 用正则，原ToolAwareSimpleAgent 用状态机(支持嵌套)，这里采用状态机版本（更健壮）。
        """
        marker="[TOOL_CALL:"
        calls: list[dict[str,str]]=[]
        start=0
        while True:
            begin= text.find(marker, start)
            if begin== -1:
                break
            tool_start = begin+len(marker)
            colon= text.find(":", tool_start)
            if colon == -1:
                break
            tool_name= text[tool_start:colon].strip()
            body_start= colon+1
            pos=body_start
            depth=0
            in_string =False
            string_quote=""
            while pos < len(text):
                char= text[pos]
                if char in {'"',"'"}:
                    if not in_string:
                        in_string = True
                        string_quote = char
                    elif char == string_quote and (pos==0 or text[pos-1]!="\\"):
                        in_string = False
                        string_quote=""
                if not in_string:
                    if char=='[':
                        depth+=1
                    elif char==']':
                        if depth==0:
                            body=text[body_start:pos].strip()
                            original= text[begin:pos+1]
                            calls.append({
                                "tool_name": tool_name,
                                "parameters":body,
                                "original":original,
                            })
                            start=pos+1
                            break
                        else:
                            depth-=1

                    pos+=1
                else:
                    start=pos
            return calls

    def _parse_tool_parameters_text(self, tool_name:str, params_str:str)->dict[str,typing.Any]:
        """解析文本模式工具参数
        支持JSON/key=value 逗号分割、简单文本
        """
        import json
        import ast as _ast

        params_str= params_str.strip()
        if not params_str:
            return {}
        try:
            result= json.loads(params_str)
            if isinstance(result,dict):
                return result
        except (json.JSONDecodeError, ValueError) as exc:
            pass
        if "=" in params_str:
            result: dict[str, typing.Any]= {}
            for pair in params_str.split(","):
                pair=pair.strip()
                if "=" in pair:
                    k,v = pair.split("=",1)
                    k= k.strip()
                    v=v.strip().strip("'、'")
                    result[k]=v
            if result:
                return result
        try:
            result= _ast.literal_eval(params_str)
            if isinstance(result,dict):
                return result
        except (ValueError, SyntaxError) :
            pass
        return {"input": params_str}

    async def _execute_text_tool_calls(self, text:str)->list[message.Msg]:
        calls=self._parse_text_tool_calls(text)
        if not calls:
            return []

        results: list[message.Msg]=[]
        for call in calls:
            tool_name= call["tool_name"]
            params= self._parse_tool_parameters_text(tool_name, call["parameters"])
            try:
                 tool= self.toolkit.get_tool(tool_name) if hasattr(self.toolkit, "get_tool") else None
                 if tool is None:
                     result_text=f"工具{tool_name} 不存在"
                 else:
                     result = tool.run(params) if hasattr(tool,"run") else tool(params)
                     result_text = f"工具{tool_name} 执行结果：\n {result}"

                 #通知监听器（tool_call_listener）
                 if self.tool_call_listener:
                     self.tool_call_listener(
                         {
                             "agent_name":self.name,
                             "tool_name": tool_name,
                             "parse_parameters":params,
                             "result":result_text,
                         }
                     )
            except Exception as exc:
                result_text= f"工具 {tool_name} 执行失败:{e}"

            result.append(message.Msg(self.name, f"工具执行结果：\n{result_text}\n\n 请基于这些结果给出完整的回答", "user"))
        return results


    @staticmethod
    def _format_prompt(template: str, **kwargs: object) -> str:
        """渲染提示词模板，并容忍 ``{task}`` / ``{question}`` 两种命名。

        ``_DEFAULT_REFLECTION_PROMPTS['initial']`` 用的是 ``{task}``，而调用点
        原先写的是 ``prompts["initial"].format(question=task)`` —— 直接
        ``KeyError: 'task'``，反思策略第一步就崩。这里同时注入两个别名，
        使内置模板与用户自定义模板（可能沿用任一种命名）都能渲染。
        """
        merged = dict(kwargs)
        task = merged.get("task", merged.get("question"))
        merged.setdefault("task", task)
        merged.setdefault("question", task)
        return template.format(**merged)

    async def _notify_text_stream_end(self) -> None:
        """``react_streaming.collect_model_turn`` 需要的流结束回调。

        ReAct 主循环里由 ``react_agent_reply`` 自己提供；这里给 text_parse /
        plan_solve / reflection 三条策略复用同一套收集器，行为与主循环对齐。
        """
        try:
            if self.model is not None and getattr(self.model, "observer", None) is not None:
                await self.model.observer.on_text_stream_end(self.name)
        except Exception as exc:
            logger.error(exc)

    async def _generate_turn(self, *, iteration: int = 0) -> react_streaming.ModelTurn:
        """执行一次完整的「格式化上下文 → 调模型 → 收集成 ModelTurn」。

        text_parse / plan_solve / reflection 三条策略原先各自用过时的同步签名
        调用模型：``self.formatter.format(self._sys_prompt, self.context)``
        —— 而 formatter 实际是 ``async def format(*, msgs)``，不 await 只会拿到
        一个协程对象；再把它交给 ``await self.model(prompt)`` 必然失败。
        这里统一成与 ReAct 主循环（``react_agent_reply._collect_turn``）
        完全一致的调用范式，三条策略这才真正可运行。
        """
        system_message = message.Msg("system", self._sys_prompt, "system")
        memory_messages = await self.context.get_messages()
        formatted = await self.formatter.format(msgs=[system_message, *memory_messages])
        tool_schema = self.toolkit.get_json_schemas() if self.toolkit else []
        stream = await self._call_model_with_error_recovery(
            formatted,
            tool_schema,
            iteration=iteration,
        )
        return await react_streaming.collect_model_turn(
            stream,
            agent_name=self.name,
            emit_text_delta=self._emit_text_delta,
            emit_tool_delta=self._emit_tool_call_streaming,
            notify_text_stream_end=self._notify_text_stream_end,
            event_logger=logger,
        )

    async def _reply_text_parse(self, msg: message.Msg)->message.Msg:
        """文本标记解析模式（原 SimpleAgent 行为）"""
        await self.context.add(msg)
        max_iters = self.max_iters or 3
        for iteration in range(max_iters):
            await self._fire_hooks("pre_reasoning",{
                "iteration": iteration, "agent_name": self.name,
            })
            turn = await self._generate_turn(iteration=iteration)
            text = turn.full_text or ""
            assistant_msg = message.Msg(self.name, text, "assistant")
            # 主循环约定：post_reasoning 的 response 必须是 Msg
            # （react_agent_reply._apply_post_reasoning_hook 会校验类型）。
            await self._fire_hooks("post_reasoning",{
                "response": assistant_msg,"agent_name":self.name,
            })

            tool_calls =self._parse_text_tool_calls(text)
            if not tool_calls:
                #无工具调用-》终局
                await self.context.add(assistant_msg)
                await self._fire_hooks("pre_print",{"text":text})
                return assistant_msg
            #有工具调用-》执行并把结果放回上下文，进入下一轮
            await self.context.add(assistant_msg)
            tool_results= await self._execute_text_tool_calls(text)
            for result_msg in tool_results:
                await self.context.add(result_msg)
            await self._fire_hooks("post_observe",{"tool_count":len(tool_calls),"agent_name":self.name, })

        #超过最大迭代-》再调一次模型做兜底总结
        turn = await self._generate_turn(iteration=max_iters)
        final_text= turn.full_text or ""
        final_msg= message.Msg(self.name, final_text, "assistant")
        await self.context.add(final_msg)
        return final_msg

    async def _reply_plan_solve(self, msg: message.Msg)->message.Msg:
        """规划执行"""
        import ast as _ast
        task = msg.content
        prompts = self.plan_prompts

        #1.规划阶段
        planner_prompt = self._format_prompt(prompts["planner"], question=task)
        await self.context.add(message.Msg(self.name, planner_prompt, "user"))
        plan_turn = await self._generate_turn(iteration=0)
        plan_text = plan_turn.full_text or ""
        #解析计划
        try:
            plan_str= plan_text.split("'''python")[1].split("'''")[0].strip()
            plan= _ast.literal_eval(plan_str)
            if not isinstance(plan,list):
                plan=[]
        except (ValueError, SyntaxError,IndexError) :
            plan=[]

        if not plan:
            return message.Msg(self.name, f"规划失败，无法分解任务:{task}", "assistant")

        #2. 执行阶段
        history=""
        final_answer=""
        for i,step in enumerate(plan,1):
            executor_prompt= self._format_prompt(prompts["executor"],
                question=task,
                plan=plan,
                history= history if history else "无",
                current_step=step,
            )
            await self.context.add(message.Msg(self.name, executor_prompt, "user"))
            step_turn = await self._generate_turn(iteration=i)
            step_result= step_turn.full_text or ""
            history+=f"步骤 {i};{step}\n 结果：{step_result}\n\n"
            final_answer+=step_result
        return message.Msg(self.name, final_answer, "assistant")

    async def _reply_reflection(self, msg:message.Msg)->message.Msg:
        """反思迭代模式"""
        task = msg.content
        prompts = self.reflection_prompts
        max_iters = self.reflection_max_iterations

        #1. 初始执行
        initial_prompt= self._format_prompt(prompts["initial"], task=task)
        await self.context.add(message.Msg(self.name, initial_prompt, "user"))
        initial_turn = await self._generate_turn(iteration=0)
        last_result= initial_turn.full_text or ""

        #2. 反思-优化循环
        for i in range(max_iters):
            reflect_prompt = self._format_prompt(prompts["reflect"],
                task=task, content=last_result,
            )
            await self.context.add(message.Msg(self.name, reflect_prompt, "user"))
            feedback_turn = await self._generate_turn(iteration=i + 1)
            feedback = feedback_turn.full_text or ""

            if "无需改进" in feedback or "no need for improvement" in feedback.lower():
                logger.info("反思认为结果已无需改进")
                break
            refine_prompt = self._format_prompt(prompts["refine"],
                task=task, last_attempt= last_result, feedback=feedback,
            )
            await self.context.add(message.Msg(self.name, refine_prompt, "user"))
            refined_turn = await self._generate_turn(iteration=i + 1)
            last_result= refined_turn.full_text or ""
        return message.Msg(self.name, last_result, "assistant")


# 兼容两种拼写：历史调用点用 ``ReActAgent``，部分编排器用 ``ReactAgent``（大写 A）。
# 这里统一以 ``ReActAgent`` 为权威定义，并导出 ``ReactAgent`` 别名。
ReactAgent = ReActAgent




































