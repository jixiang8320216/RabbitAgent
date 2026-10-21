import collections
import json
import pydantic
import typing
import domains.agent.domain.value_objects.message as message_module
type JsonObject= message_module.JsonObject

class ThinkingInputParts(pydantic.BaseModel):
    system_instructions: list[str] = pydantic.Field(default_factory=list)
    message_history: list[str] = pydantic.Field(default_factory=list)
    tool_calls: list[str] = pydantic.Field(default_factory=list)

def project_thinking_input(
    formatted_messages: collections.abc.Sequence[JsonObject],
)->JsonObject:
    parts=ThinkingInputParts()
    for formatted_message in formatted_messages:
        _append_message(parts,formatted_message)
    raw_prompt= json.dumps(formatted_message, indent=2, ensure_ascii=False)
    raw_prompt_len = len(raw_prompt)
    if raw_prompt_len>10_000:
        raw_prompt = raw_prompt[:10_000]+"\n...(truncated)"
    return {
        "system_instructions": "\n\n---\n\n".join(parts.system_instructions),
        "message_history": "\n\n".join(parts.message_history),
        "tool_calls": "\n".join(parts.tool_calls),
        "raw_prompt": raw_prompt,
        "raw_prompt_len": raw_prompt_len,
        "optimization_info":{
            "original_prompt":"",
            "optimized_prompt":"",
            "optimization_applied":False,
        }
    }

def _append_message(parts: ThinkingInputParts,formatted_message: JsonObject)->None:
    role= formatted_message.get("role")
    text_content= _text_content(formatted_message.get("content"))
    if role=="system":
        if text_content.strip():
            parts.system_instructions.append(text_content)
        return
    if role=="user":
        parts.message_history.append(f"[User]:{_truncate(text_content,500)}")
        return
    if role=="assistant":
        # 原写作 _append_message(parts,formatted_message,text_content)，但
        # _append_message 只有 2 个形参，处理 assistant 分支的真正实现是下面
        # 已定义的 _append_assistant(parts, formatted_message, text_content)。
        # 该TypeError 在 assistant 消息存在时（正常对话第二轮起必现）抛出会让
        # 整条 turn 失败——此前被 message_formatters 的缩进缺陷掩盖，
        # 因为格式化阶段只输出 1 条 system，永远走不到 assistant 分支。
        _append_assistant(parts,formatted_message,text_content)

def _append_assistant(
    parts: ThinkingInputParts,
    formatted_message: JsonObject,
    text_content: str,
)->None:
    parts.tool_calls.extend(_tool_call_lines(formatted_message.get("tool_calls")))
    reasoning= formatted_message.get("reasoning-content")
    if isinstance(reasoning,str) and reasoning:
        parts.message_history.append(f"[Assistant Reasoning]:{_truncate(reasoning,300)}")
    if text_content:
        parts.message_history.append(f"[Assistant]: {_truncate(text_content,500)}")


def  _text_content(content:pydantic.JsonObject|None)->str:
    if isinstance(content,str):
        return content
    if not isinstance(content,list):
        return ""
    parts:list[str]= []
    for block in content:
        if not isinstance(block,dict) or block.get("type")!="text":
            continue
        text= block.get("text")
        if isinstance(text,str):
            parts.append(text)
    return "".join(parts)

def _tool_call_lines(tool_calls:pydantic.JsonValue|None)->list[str]:
    if not isinstance(tool_calls,list):
        return []
    lines: list[str]= []
    for tool_call in tool_calls:
        line=_tool_call_line(tool_call)
        if line is not None:
            lines.append(line)
    return lines

def _tool_call_line(tool_call:pydantic.JsonValue)->str|None:
    if not isinstance(tool_call,dict):
        return None
    function= tool_call.get("function")
    if not isinstance(function,dict):
        return None
    raw_name=function.get("name")
    tool_name = raw_name if isinstance(raw_name,str) else "unknown"
    raw_arguments =function.get("arguments")
    tool_arguments = raw_arguments if isinstance(raw_arguments,str) else ""
    return f"[Tool Call] {tool_name}: {_truncate(tool_arguments,200)}"

def _truncate(value:str,limit:int)->str:
    return value[:limit] +"..." if len(value)>limit else value

def as_formatted_messages(messages:list[JsonObject])->list[JsonObject]:
    return typing.cast(list[JsonObject],messages)























