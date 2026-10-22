
from __future__ import annotations

import collections.abc
import typing

import pydantic

__all__ = ["ToolInvocationScope"]

import domains.runtime.application.hook_lifecycle as hook_lifecycle_module
import domains.agent.domain.aggregates.cancellation as cancel_scope
import domains.agent.domain.value_objects.message as message
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
import domains.agent.domain.value_objects.tool_response as tool_response
from domains.observability.domain import tool_result_projection

type JsonObject = dict[str, pydantic.JsonValue]

class ToolInvocationObserver(typing.Protocol):

    async def on_tool_call_start(
        self,
        agent_name:str,
        tool_name:str,
        args_summary:str,
        tool_id:str| None=None,
        timeout:int|None=None,
        cwd:str|None=None,
        timestamp:str|None=None,
        thinking_operation_id:str|None=None,
        parent_instance_id:str|None=None,
        model_id:str|None=None,
        display_name:str|None=None,
    )->None:...

    async def on_tool_call_end(
            self,
            agent_name: str,
            tool_name: str,
            tool_id: str | None = None,
            timestamp: int | None = None,
            error: str | None = None,
            result_summary: str | None = None,
            parent_instance_id: str | None = None,
            model_id: str | None = None,
            display_name: str | None = None,
            external_data_cards: list[dict[str, str|None]] | None = None,
            structured_result: tool_result_projection.StructuredToolResult|None = None,
    ) -> None: ...

    async def on_tool_output_streaming(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:str,
        delta:str,
        stream_kind:str,
        operation_id:str,
        instance_id:str,
    )->None:...

class ToolInvocationScope:
    """单次工具调用的作用域（只读句柄）。

    :param tool_use: 工具调用块：``ToolUseBlock`` / ``collections.abc.Mapping``
        / id 字符串 / ``None``。Mapping 与对象都按 ``id`` / ``name`` / ``input``
        三个键读取。
    :param response: 该次调用的结果：``ToolResponse`` / ``ToolResultBlock`` /
        Mapping / ``Msg`` 均可，仅在序列化时读取，不解析成具体模型。
    :param tool_use_id: 调用标识；给了就覆盖 ``tool_use`` 里的同名值。
    :param tool_name: 工具名；同上。
    :param tool_input: 工具入参；同上（非 Mapping 视为无效，落回 ``{}``）。
    :param invocation_id: 本次 agent 调用标识（hook 上报用）。
    :param session_id: 所属会话标识。
    :param turn_id: 所属轮次标识。
    :param agent_name: 发起调用的 agent 名。
    """
    def __init__(
        self,
        parent:cancel_scope.CancelScope,
        tool_use: message.ToolUseBlock,
        *,
        agent_name:str,
        observer:ToolInvocationObserver|None,
        telemetry_emitter: runtime_ports.ToolTelemetryEmitterProtocol|None=None,
        tool_classification_resolver: runtime_ports.ToolClassificationResolverProtocol|None=None,
        tool_observability_metadata_resolver: runtime_ports.ToolObservabilityMetadataResolverProtocol|None=None,
        tool_args_summary_resolver: runtime_ports.ToolArgsSummaryResolverProtocol|None=None,
        thinking_operation_id: typing.Optional[str] = None,
        invocation_id: typing.Optional[str] = None,
        model_id: typing.Optional[str] = None,
        hook_lifecycle: hook_lifecycle_module.HookLifecycleCoordinator|None=None,
        hook_metadata: hook_lifecycle_module.HokLifecycleMetadata|None=None,
    ) -> None:
        self._parent = parent
        self._tool_use = tool_use
        self._agent_name = agent_name
        self._tool_name =tool_use.name
        self._observer = observer
        self._telemetry = telemetry_emitter
        self._tool_classification_resolver = tool_classification_resolver
        self._tool_args_summary_resolver = tool_args_summary_resolver
        self._tool_observability_metadata_resolver = tool_observability_metadata_resolver
        self._thinking_op_id = thinking_operation_id
        self._invocation_id = invocation_id
        self._model_id = model_id
        self._hook_lifecycle = hook_lifecycle
        self._hook_metadata = hook_metadata

        self._display_name:typing.Optional[str] = None
        if self._tool_name.startswith("mcp__"):
            parts= self._tool_name.split("__",2)
            if len(parts) == 3:
                self._display_name = parts[2]
        tool_input= tool_use.input
        self._tool_input = tool_input
        self._args_summary = self._compute_args_summary(self._tool_name, tool_input)
        timeout=tool_input.get("timeout")
        self._timeout = timeout if isinstance(timeout,int) and not isinstance(timeout,bool) else None
        cwd = tool_input.get("cwd")
        self._cwd = cwd if isinstance(cwd,str) else None
        self._start_time:float=0.0
        self._start_ts:str=""
        self._scope = typing.Optional[cancel_scope.CancelScope]=None
        self._token=None
        self._tool_identity_token=None
        self._tool_output_stream_token=None
        self._hook_context_token=None
        self._approval_policy_token=None
        self._approval_policy_snapshot:frozenset[str]|None=None
        self._last_response: tool_response.ToolResponse|None=None
        self._blocked_reason: str| None=None
        self._pre_additional_context:tuple[str,...]=()
        self._permission_action="continue"
        self._pre_run_ids:tuple[str,...]=()
        self._prepared=False

    # --- 只读属性 ---------------------------------------------------------

    @property
    def tool_use_id(self) -> str | None:
        """工具调用标识（等价于 ``ToolUseBlock.id``）。"""
        return self._tool_use_id

    @property
    def tool_name(self) -> str | None:
        """工具名。"""
        return self._tool_name

    @property
    def tool_input(self) -> dict[str, object]:
        """工具入参（浅拷贝，改它不会影响本作用域）。"""
        return dict(self._tool_input)

    @property
    def response(self) -> object:
        """该次调用的原始结果对象（未归一化）。"""
        return self._response

    @property
    def invocation_id(self) -> str | None:
        return self._invocation_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def turn_id(self) -> str | None:
        return self._turn_id

    @property
    def agent_name(self) -> str | None:
        return self._agent_name

