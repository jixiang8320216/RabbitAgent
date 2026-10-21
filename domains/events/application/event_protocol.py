import pydantic
import domains.events.domain.event_payloads as event_payloads
from domains.observability.domain import tool_result_projection


class EventProtocol:


    @staticmethod
    def build_agent_text(
        agent_name: str,
        content: str,
        is_streaming:bool=True,
        operation_id:str| None=None,
        instance_id:str| None=None,
    )->dict[str,pydantic.JsonValue]:
        return event_payloads.AgentTextPayload(
            agent_name=agent_name,
            content=content,
            is_streaming=is_streaming,
            operation_id=operation_id or None,
            instance_id=instance_id or None,

        ).to_wire_data()

    @staticmethod
    def build_tool_execution(
        agent_name: str,
        tool_name: str,
        is_start: bool,
        tool_id: str| None=None,
        args_summary: str| None=None,
        timeout: int| None=None,
        cwd: str| None=None,
        timestamp: str| None=None,
        error: str| None=None,
        result_summary: str| None=None,
        thinking_operation_id: str| None=None,
        parent_operation_id: str| None=None,
        model_id: str| None=None,
        display_name:str| None=None,
        external_data_cards:list[dict[str,str|None]]| None=None,
        structured_result: tool_result_projection.StructuredToolResult |None=None,
    )->dict[str,pydantic.JsonValue]:
        values: dict[str,object] = {
            "agent_name": agent_name,
            "tool_name": tool_name,
            "is_start": is_start,
            "tool_id": tool_id or None,
            "args_summary": args_summary or None,
            "timeout": timeout,
            "cwd": cwd or None ,
            "error": error or None,
            "result_summary":result_summary or None,
            "thinking_operation_id": thinking_operation_id or None,
            "parent_operation_id": parent_operation_id or None,
            "model_id": model_id or None,
            "display_name": display_name or None,
            "external_data_cards": external_data_cards,
            "structured_result": structured_result ,
        }
        if timestamp is not None:
            values["timestamp"] = timestamp
        return event_payloads.ToolExecutionPayload.model_validate(values).to_wire_data()