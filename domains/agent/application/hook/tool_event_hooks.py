import collections.abc as abc
import logging

import pydantic
import json
import domains.configuration.domain.protocol.config_protocol as config_protocol
import domains.agent.domain.aggregates.react_agent as agent_module
import domains.agent.application.hook.runtime_hook_contracts as hook_contracts
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.agent.domain.value_objects.message as message
import domains.agent.domain.value_objects.tool_response as tool_response_module
import time
import datetime as datetime_module
import domains.agent.application.context.incomplete_response_validator as incomplete_response_validator
from domains.observability.application import observability_context
import domains.llm.provider.application.formatting.tool_string_truncation as tool_string_truncation
type ToolArgsSummaryFactory = abc.Callable[[str,abc.Mapping[str,object],str], str]

logger = logging.getLogger(__name__)

class _CommandExecutionResult(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="ignore")

    returncode:int |None=None
    stdout:str=""
    stderr:str=""

class _CommandToolPayload(pydantic.BaseModel):
    model_config= pydantic.ConfigDict(extra="ignore")

    result: _CommandExecutionResult = pydantic.Field(default_factory=_CommandExecutionResult)

class AgentToolEventHookAttacher:

    def __init__(
        self,
            *,
        config_manager: config_protocol.ConfigManagerProtocol,
        args_summary_factory:ToolArgsSummaryFactory,
        )->None:
        self._config_manager = config_manager
        self._args_summary_factory = args_summary_factory

    def attach_start(self, agent: agent_module.ReActAgent)-> hook_contracts.ToolHookState:
        hook_state = hook_contracts.ToolHookState()
        async def send_tool_start_event_hook(
            agent_self: agent_module.ReActAgent,
            kwargs: dict[str,object],
        )->dict[str,object]| None:
            del agent_self
            try:
                return await self._run_start(agent,hook_state,kwargs)
            except Exception as e:
                logger.error(e)
                return None

        agent.register_instance_hook("pre_acting","send_tool_start_event",send_tool_start_event_hook)
        return hook_state

    def attach_end(
        self,
        agent: agent_module.ReActAgent,
        hook_state: hook_contracts.ToolHookState,
    ) -> None:
        async def send_tool_end_event_hook(
            agent_self:agent_module.ReActAgent,
            kwargs:dict[str,object],
        )->None:
            del agent_self
            await  self._run_end(agent, hook_state, kwargs)
        agent.register_instance_hook("post_acting","send_tool_end_event", send_tool_end_event_hook)

    async def _run_start(
        self,
        agent: agent_module.ReActAgent,
        hook_state: hook_contracts.ToolHookState,
        kwargs: dict[str, object],
    ) -> dict[str,object]|None:
        tool_call_value = kwargs.get("tool_call")
        if not isinstance(tool_call_value, message.ToolUseBlock):
            return None
        tool_call = tool_call_value
        if tool_call.name != "generate_response":
            _reset_response_counters(agent,hook_state, tool_call.name)
        else:
            blocked_call = _blocked_incomplete_response(agent, hook_state, tool_call)
            if blocked_call is not None:
                return {"tool_call": blocked_call}
        execution_context.set_current_agent_name(agent.name)
        args_summary= self._build_args_summary(tool_call)
        hook_state.args_cache[tool_call.id]= args_summary
        start_timestamp = datetime_module.datetime.now().isoformat()
        hook_state.execution_timestamps[tool_call.id] = time.time()
        logger.debug("sending start event for tool_id=%s timestamp=%s",tool_call.name, start_timestamp)
        return None

    @staticmethod
    def _run_end(
            agent: agent_module.ReActAgent,
            hook_state: hook_contracts.ToolHookState,
            kwargs: dict[str, object],
    )->None:
        tool_results = _tool_results_from_context(kwargs["tool_results"])
        for tool_use, tool_response in tool_results:
            hook_state.execution_timestamps.pop(tool_use.id,None)
            hook_state.args_cache.pop(tool_use.id,None)
            error_message = _tool_error_message(tool_use, tool_response)
            _increment_tool_count(agent, tool_use.name)
            execution_context.set_current_agent_name(None)
            _log_tool_completion(tool_use, error_message)

    def _build_args_summary(self, tool_call: message.ToolUseBlock) -> str:
        max_args_bytes = self._config_manager.get_property(
            "tool_args_compression_limit",
            -1,
            experted_type=int,
        )
        if max_args_bytes>=0:
            args_summary= tool_string_truncation.ToolStringTruncator.truncate_tool_arguments(
                json.dumps(tool_call.input, ensure_ascii=False),
                max_bytes= max_args_bytes,
                tool_name=tool_call.name,
            )
        elif tool_call.name == "execute_command" and tool_call.input:
            args_summary=json.dumps(
                {"command":tool_call.input.get("command","")}, ensure_ascii=False)
        else:
            args_summary= json.dumps(tool_call.input, ensure_ascii=False)
        if len(args_summary)<=2000 or tool_call.name in ("write","udpate"):
            return args_summary
        return self._args_summary_factory(tool_call.name, tool_call.input, args_summary)




def _tool_error_message(
    tool_use: message.ToolUseBlock,
    tool_response:tool_response_module.ToolResponse,
    ) -> str | None:
    result_text = " ".join(
        item.text for item in tool_response.content if isinstance(item, message.TextBlock)
    )

    if not result_text:
        return None
    if "<system-info>The tool call has been interrupted by the user. </system-info>" in result_text:
       return "Task interrupted by user"
    try:
        result = _CommandToolPayload.model_validate_json(result_text).result
    except pydantic.ValidationError as exc:
        logger.debug("[TOOL_EVENT_HOOK] tool error check failed: %s", exc)
        return None
    if result.returncode is None or result.returncode == 0:
        return None
    detail = result.stderr or result.stdout
    base_message=f"Command failed with exit code {result.returncode}"
    return f"{base_message}: {detail}"if detail else base_message




def _increment_tool_count(
    agent: agent_module.ReActAgent,
    tool_name: str,
) -> None:
    try:
        resolver = agent.tool_classification_resolver
        skip_count=False
        if resolver is not None:
            try:
                classification = resolver.resolve_tool_classification(tool_name)
                skip_count= classification is not None and classification.tool_type=="sub_agent"
            except Exception as exc:
                logger.debug(exc)
        if not skip_count:
            observability_context.increment_tool_call_count()
    except Exception as exc:
        logger.error(exc)


def _log_tool_completion(
    tool_use: message.ToolUseBlock,
    error_message: str | None,
) -> None:
   if error_message :
       logger.warning("tOOL END WITH ERROR : id:%s  %s", tool_use.id, error_message)
   else:
       logger.debug("Tool End successfully %s  %s", tool_use.id,tool_use.name)


# --- 追加：``generate_response`` 响应计数 / 空响应拦截 -----------------------
#
# 下面这一组常量 + 工具函数服务于三个入口：
#
# - ``AgentToolEventHookAttacher._build_args_summary``：把工具入参压成一行摘要；
# - ``_reset_response_counters``：模型调了非 ``generate_response`` 的工具 →
#   响应计数清零（一次"最终答复"的尝试被打断了）；
# - ``_blocked_incomplete_response``：拦下正文为空的 ``generate_response``。
#
# 设计约束与文件里已有的函数保持一致：**一切失败只记日志**。hook 是观测性的，
# 绝不能因为计数/拦截这类旁路逻辑打断一轮对话。

_RESPONSE_TEXT_KEYS=(
    "text",
    "response",
    "content",
    "message",
    "answer",
    "output",
    "final_response",
)

# 连续被拦几次之后放行：模型若一直吐空参数，无限拦会让它原地空转。
_MAX_BLOCKED_RESPONSE_ATTEMPTS=3

_BLOCKED_RESPONSE_MESSAGE =(
    "generate_response was blocked: the response content is empty. Continue "
    "working on the task, or call generate_response again with a non-empty "
    "'text' argument containing your complete answer."
)

# hook_state 上可能挂着的响应计数。逐个探测，不存在就跳过——不给 ToolHookState
# 强加字段。后三个是当前 ToolHookState 真实持有的字段与拦截计数。
_RESPONSE_COUNTER_ATTRS=(
    "response_count",
    "generate_response_count",
    "pending_response_text",
    "pending_response_id",
    "response_started_at",
    "response_text_length",
    "consecutive_plan_text_count",
    "incomplete_response_count",
    "blocked_response_count",
)

# agent 身上可能挂着的"待定答复"标记（公共名 + 私有名都试一遍）。
_AGENT_RESPONSE_COUNTER_ATTRS=(
    "pending_response_text",
    "pending_response_id",
    "_pending_response_text",
    "_pending_response_id",
)

def _is_mapping(value:object)->bool:
    """判 ``Mapping``。

    文件顶部是 ``import collections.abc as abc``，而 ``Mapping`` 在
    ``collections.abc`` 里（``abc.Mapping`` 并不存在）。为了不动顶部的 import，
    这里在**函数内**现取 ``collections.abc``——``sys.modules`` 命中，开销可忽略。
    """
    import collections.abc as collections_abc
    return isinstance(value, collections_abc.Mapping)

def _set_attr(target:object,name:str,value:object)->bool:
    """带保护地写属性；``target`` 为 ``None`` 或有 ``__slots__`` 时静默失败。"""
    if target is None:
        return False
    try:
        setattr(target,name,value)
    except Exception as exc:
        logger.debug("[TOOL_EVENT_HOOK] set %s failed on %s: %s",name,type(target).__name__,exc)
        return False
    return True

def _tool_call_field(tool_call:object,field:str,default:object=None)->object:
    """取工具调用上的字段（``id`` / ``name`` / ``input``），兼容两种形状。"""
    if _is_mapping(tool_call):
        value=tool_call.get(field)
    else:
        value=getattr(tool_call,field,None)
    return default if value is None else value

def _tool_call_input(tool_call:object)->object:
    """取工具调用的入参（``input``），兼容 Mapping 与对象两种形状。"""
    return _tool_call_field(tool_call,"input")

def _serialize_tool_args(tool_input:object)->str:
    """把入参序列化成字符串。

    只用于给摘要工厂算"参数有多大"（见
    ``agent_runtime_hook_support._large_argument_note``），所以失败就退回
    ``str()``，绝不外抛。``json`` 同样在函数内现取，不动顶部 import。
    """
    import json
    try:
        return json.dumps(tool_input,ensure_ascii=False,default=str)
    except Exception:
        return str(tool_input)

def _response_text_of(tool_call:object)->str:
    """从 ``generate_response`` 的入参里取正文；取不到返回空串。

    按 :data:`_RESPONSE_TEXT_KEYS` 依次找，命中一个非空字符串就收工；若该键
    挂着的是嵌套 Mapping（模型把正文包了一层），再往下取一层的 ``text``。
    """
    raw_input = _tool_call_input(tool_call)
    if isinstance(raw_input,str):
        return raw_input.strip()
    if not _is_mapping(raw_input):
        return ""
    for key in _RESPONSE_TEXT_KEYS:
        value=raw_input.get(key)
        if isinstance(value,str) and value.strip():
            return value.strip()
        if not _is_mapping(value):
            continue
        nested=value.get("text")
        if isinstance(nested,str) and nested.strip():
            return nested.strip()
    return ""

def _reset_response_counters(
    agent: agent_module.ReActAgent,
    hook_state: hook_contracts.ToolHookState,
    tool_name: str,
) -> None:
     if hook_state.consecutive_plan_text_count>0:
         hook_state.consecutive_plan_text_count=0
     if hook_state.incomplete_response_count>0:
        hook_state.incomplete_response_count=0

def _blocked_incomplete_response(
    agent: agent_module.ReActAgent,
    hook_state: hook_contracts.ToolHookState,
    tool_call: message.ToolUseBlock,
) -> message.ToolResultBlock | None:
    response_value= tool_call.get("response")
    response_content= response_value if isinstance(response_value,str) else ""
    validation = incomplete_response_validator.validate_response(response_content,has_tool_calls=False)
    if not validation.is_incomplete or validation.confidence<0.7:
        return None
    hook_state.incomplete_response_count+=1
    if hook_state.incomplete_response_count>=3:
        hook_state.incomplete_response_count=0
        return None
    return message.ToolUseBlock(
        type="tool_use",
        id=tool_call.id,
        name="_INCOMPLETE_RESPONSE_BLOCKED__",
        input={
            "__error_type__": "incomplete_response",
            "_error_message__":(
               f"Your response was blocked because it appears to be incomplete. Pattern:"
               f"'{validation.match_pattern}'. Please execute the actual tool calls first,"
               "then call generate _response with real results"
             ),
            "__original_response_preview__": response_content[:300],
        },
        index=tool_call.index,
    )

def _tool_results_from_context(
    value:object,
)->list[tuple[message.ToolUseBlock, tool_response_module.ToolResponse]]:
    if not isinstance(value,list):
        return []
    return [
        (item[0], item[1])
        for item in value
        if isinstance(item,tuple)
        and len(item)==2
        and isinstance(item[0],message.ToolUseBlock)
        and isinstance(item[1],tool_response_module.ToolResponse)
    ]




















