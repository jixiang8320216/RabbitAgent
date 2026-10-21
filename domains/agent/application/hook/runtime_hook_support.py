import enum
import json
import logging
import typing

import pydantic

import domains.agent.application.hook.runtime_hook_contracts as hook_contracts
import domains.agent.domain.aggregates.react_agent as agent_module

logger = logging.getLogger(__name__)

def should_send_generate_response(
    agent_name:str,
    app_instance:object|None,
    *,
    root_agent_id:str|None,
    is_root_invocation:bool|None,
)->bool:
    if app_instance is None:
        return _without_app_generate_response_decision(agent_name, root_agent_id)
    app_mode= app_instance.app_mode if isinstance(app_instance, hook_contracts.AppModeSource) else None
    if app_mode =="guarded":
        allowed="guard" in agent_name.lower()
        if not allowed:
            logger.debug("Filtering response for non-guraar agent")
        return allowed
    if app_mode  in {"delegate", "roleplay"}:
        return _delegate_generate_response_decision(
            agent_name,
            root_agent_id=root_agent_id,
            is_root_invocation=is_root_invocation,
        )
    return True

def _without_app_generate_response_decision(agent_name:str, root_agent_id:str|None)->bool:
    if root_agent_id:
        allowed=agent_name==root_agent_id
        if not allowed:
            logger.debug("Filtering response for non-guraar agent")
        return allowed
    return True

def _delegate_generate_response_decision(
    agent_name:str,
    *,
    root_agent_id:str|None,
    is_root_invocation:bool|None,
)->bool:
    if is_root_invocation is not None:
        return is_root_invocation
    allowed= bool(root_agent_id and agent_name==root_agent_id)
    if not allowed:
        logger.debug("Filtering response for non-guraar agent")
    return allowed

def  create_tool_args_summary(
    tool_name:str,
    tool_input:typing.Mapping[str, object],
    original_args:str,
)->str:
    try:
        summary= _tool_args_summary_fields(tool_name, tool_input, original_args)
        return json.dumps(summary, ensure_ascii=False)
    except Exception as e:
        logger.error(e)
        return json.dumps({"tool":tool_name, "_note": _large_argument_note(original_args)}, ensure_ascii=False)

def _tool_args_summary_fields(
    tool_name:str,
    tool_input:typing.Mapping[str, object],
    original_args:str,
)->dict[str,object]:
    summary:dict[str,object] = {"tool":tool_name}
    file_path=_string_field(tool_input,"file_path")
    if file_path:
        summary["file"] = file_path
    if tool_name in ("write","update"):
        _add_code_change_summary(summary, tool_input, original_args)
    elif tool_name =="execute_command":
        command = _string_field(tool_input,"command")
        if command:
            summary["command"] = _truncate(command,100)
    else:
        present_keys= _present_bounded_keys(tool_input)
        if present_keys:
            summary["params"] = present_keys[:5]+(
                [f"...({len(present_keys)-5} more)"] if len(present_keys)>5 else []
            )
        summary["_note"] = _large_argument_note(original_args)
    return summary

def _add_code_change_summary(
    summary:dict[str, object],
    tool_input:typing.Mapping[str,object],
    original_args:str
)->None:
    code_content= _string_field(tool_input,"code_content")
    description= _string_field(tool_input,"description")
    if code_content:
        summary["lines"] = len(code_content.splitlines())
    if description:
        summary["description"] = _truncate(description,100)
    summary["_note"] = _large_argument_note(original_args)

def  _present_bounded_keys(tool_input:typing.Mapping[str, object])->list[str]:
    large_fields={"code_content", "content","old_string","new_string","text"}
    return [key for key,value in tool_input.items() if value and key not in large_fields and len(str(value))<500]

def  _string_field(values: typing.Mapping[str,object], name:str)->str:
    value = values.get(name)
    return value if isinstance(value, str) else ""

def _truncate(value:str, limit:int)->str:
    return f"{value[:limit]}..." if len(value)>limit else value

def _large_argument_note(original_args:str)->str:
    return f"Large parameters ({len(original_args)} chars) - see result for details"
#扩展新增的方法
class AppModeContract(pydantic.BaseModel):
    """agent 在某一运行模式下的身份快照（mode / role / skill）。

    作为「身份三要素」的轻量载体保留；真正被遥测调用点使用的是模块级函数
    :func:`determine_skill_and_role`，它返回 ``(skill_name, role)`` 二元组。
    """

    mode: str = ""
    role: str = ""
    skill: str = ""


def determine_skill_and_role(
    agent: agent_module.ReActAgent,
    app_instance: object | None,
    is_root_agent: bool,
) -> tuple[str, str]:
    app_mode= app_instance.app_mode if isinstance(app_instance, hook_contracts.AppModeSource) else None
    if app_mode in {"delegate","roleplay"}:
        return "","delegate"
    if app_mode =="guard":
        return "", "guard"
    if app_mode == "roleplay":
        return "", "roleplay"
    skill_name = _default_skill_name(agent, app_instance)
    if "guard" in agent.name.lower():
        return skill_name, "guard"
    return skill_name,"plan" if is_root_agent else "skill"

def _default_skill_name(agent:  agent_module.ReActAgent, app_instance: object | None) -> str:
    # hook_contracts 里并没有 AgentInstanceDirectory 这个协议，原判断直接抛
    # "module 'agent.service.agent_runtime_hook_contracts' has no attribute
    # 'AgentInstanceDirectory'"。这里改为按能力探测：宿主对象上确实存在
    # agent_instance 容器时才继续解析 skill 名，否则返回空串（旁路观测，不阻断）。
    container = _safe_get_attr(app_instance, "agent_instance")
    if container is None:
        return ""
    getter = getattr(container, "get", None)
    if getter is None:
        return ""
    instance = getter(agent.name)
    # 原写法 "isinstance is None" 恒为 False，是一处笔误，导致 instance 为 None 时
    # 会在下一行抛 AttributeError。改为显式判空。
    if instance is None:
        return ""
    skill_refs = _safe_get_attr(_safe_get_attr(instance, "spec"), "skill_refs")
    if not skill_refs:
        return ""
    return skill_refs[0]

def _resolve_skill_name(agent: object, app_instance: object | None) -> str:
    """按“最具体 → 最笼统”的顺序取一个非空的 skill 名。"""
    candidate = _first_non_empty_str(
        _safe_get_attr(agent, "skill_name"),
        _safe_get_attr(_safe_get_attr(agent, "agent_context"), "skill_name"),
        _safe_get_attr(app_instance, "skill_name"),
        _safe_get_attr(agent, "name"),
    )
    return candidate or ""


def _resolve_role(
    agent: object,
    app_instance: object | None,
    is_root_agent: bool,
) -> str:
    """按“显式角色 → 运行模式 → root/sub_agent 兜底”的顺序取角色。"""
    raw = _first_non_empty_str(
        _safe_get_attr(agent, "agent_role"),
        _safe_get_attr(_safe_get_attr(agent, "agent_context"), "agent_role"),
        _safe_get_attr(agent, "role"),
        _safe_get_attr(_safe_get_attr(agent, "agent_context"), "role"),
        _safe_get_attr(app_instance, "app_mode"),
    )
    if raw:
        return raw
    return "root" if is_root_agent else "sub_agent"


def _safe_get_attr(obj: object, name: str) -> object:
    """安全读属性；``None`` / 抛异常都返回 ``None``。"""
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _first_non_empty_str(*values: object) -> str:
    """取第一个“非空字符串”；``Enum`` 自动用 ``.value``。"""
    for value in values:
        if value is None:
            continue
        text = value.value if isinstance(value, enum.Enum) else value
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""