import collections.abc
import json

import pydantic
import domains.llm.provider.infrastructure.foundation.chat_transport.chat_model as chat_model
import abc

import domains.llm.provider.infrastructure.foundation.chat_transport.contracts as transport_contracts
import domains.llm.provider.application.authentication.request_attestation as provider_request_attestation
import domains.llm.provider.infrastructure.foundation.chat_transport.failure_classification as failure_classification
import domains.agent.domain.aggregates.runtime_observer as callbacks
import domains.agent.domain.value_objects.plugin_hooks as runtime_plugin_hooks
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
from domains.agent.domain.aggregates import execution_context
from domains.observability.application import guard
_INTERNAL_OPERATION_KINDS= frozenset({"context_optimization","topic_generation"})
_JSON_OBJECT_ADAPTER= pydantic.TypeAdapter(chat_model.JsonObject)

def _content_to_text(content:object)->str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str]=[]
        for block in content:
            if isinstance(block, dict):
                text= block.get("text") or block.get("content") or block.get("thinking")
                if text:
                    parts.append(str(text))
                elif block.get("type"):
                    parts.append(json.dumps(block,ensure_ascii=False, default=str))
            elif block is not None:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    return json.dumps(content,ensure_ascii=False, default=str)

def _safe_json_loads(value:object)->object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value

def _tool_call_from_message(tool_call:object)->dict[str,object]:
    if not isinstance(tool_call, dict):
        return None
    function= tool_call.get("function")
    if isinstance(function, dict):
        return {
            "id": tool_call.get("id"),
            "name": str(function.get("name") or "unknown"),
            "args":_safe_json_loads(function.get("arguments")),
        }
    return {
        "id": tool_call.get("id"),
        "name": str(function.get("name") or "unknown"),
        "args": _safe_json_loads(tool_call.get("input") or tool_call.get("arguments")),
    }


def _tool_name_from_schema(tool:object)->str|None:
    if not isinstance(tool, dict):
        return None
    function = tool.get("function", None)
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(tool["name"])if tool.get("name") else None

def _tool_description_from_schema(tool:object)->str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if isinstance(function, dict) :
        return str(function.get("description") or "")
    return str(tool.get("description") or "")

def _build_thinking_prompt_snapshot(
    messages:list[chat_model.JsonObject],
    tools: list[chat_model.JsonObject]|None,
)->str:
    system_parts:list[str]=[]
    history:list[dict[str,str]]=[]
    tool_calls: list[dict[str,object]]=[]
    available_tools:list[dict[str,str]]=[]
    for tool in tools or []:
        name= _tool_name_from_schema(tool)
        if name:
            available_tools.append({"name":name,"description":_tool_description_from_schema(tool)})
    for message in messages:
        role= str(message.get("role") or "unknown")
        text = _content_to_text(message.get("content"))
        if role=="system":
            if text.strip():
                system_parts.append(text)
        else:
            history.append({"role":role,"content":text})
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            for raw_tool_call in raw_tool_calls:
                normalized = _tool_call_from_message(raw_tool_call)
                if normalized is not None:
                    tool_calls.append(normalized)
    snapshot={
        "system_instructions":"\n\n---\n\n".join(system_parts),
        "message_history":history,
        "tool_calls":tool_calls,
        "available_tools":available_tools,
        "raw_prompt":messages,
        "optimization_info":{
            "messageCount":len(messages),
            "toolCount":len(available_tools),
        }
    }
    return json.dumps(snapshot, ensure_ascii=False, default=str)

class ProviderChatModelBase(chat_model.ChatModel):

    def __init__(
        self,
        model_name:str,
        api_key:str |None=None,
        base_url:str|None=None,
        token_counter:object|None=None,
        agent_name:str="",
        max_retries_on_empty:int=5,
        http_proxy: str|None=None,
        https_proxy: str|None=None,
        chunk_idle_timeout:int=180,
        stream: bool=True,
        ssl_verify: bool=False,
        default_headers:collections.abc.Mapping[str,str]|None=None,
        auth_hook: transport_contracts.AuthHook|None=None,
        model_identity_resolver: transport_contracts.ModelIdentifyResolver|None=None,
        request_attestor:provider_request_attestation.ProviderRequestAttestorProtocol|None=None,
        llm_provider:str|None=None,
        failure_policy: failure_classification.ProviderFailurePolicy|None=None,
        observer:callbacks.RuntimeObserver|None=None,
        trust_env: bool|None=None,
        **model_kwargs:object,
        )->None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.token_counter = token_counter
        self.agent_name = agent_name
        self.max_retries_on_empty = max_retries_on_empty
        self._http_proxy = http_proxy
        self._https_proxy = https_proxy
        self._trust_env = trust_env
        self.ssl_verify = ssl_verify
        self.chunk_idle_timeout = chunk_idle_timeout
        self.stream = stream
        self._default_headers = dict(default_headers) if default_headers else {}
        self._auth_hook = auth_hook
        self._model_identity_resolver = model_identity_resolver
        self._request_attestor = request_attestor
        self.llm_provider = llm_provider
        self.failure_policy = failure_policy
        self.observer:callbacks.RuntimeObserver = observer or callbacks.NoOPRuntimeObserver()
        raw_generate_options = model_kwargs.pop('generate_kwargs', None)
        if raw_generate_options is not None and not isinstance(raw_generate_options, dict):
            raise TypeError('generate_kwargs must be a dict')
        self._init_generate_kwargs: transport_contracts.ProviderOptions = dict(raw_generate_options or {})
        self.model_kwargs: transport_contracts.ProviderOptions = model_kwargs
        self._auth_api_key:str|None=None
        self._auth_headers:dict[str,str]={}

    async def _send_thinking_start(
        self,
        operation_id:str,
        messages: list[chat_model.JsonObject],
        tools: list[chat_model.JsonObject]|None,
        operation_kind: str|None=None,
       )->None:
        if operation_kind in  _INTERNAL_OPERATION_KINDS:
            return
        invocation_id = execution_context.get_current_invocation_id()
        if not invocation_id:
            if isinstance(self.observer, callbacks.NoOPRuntimeObserver):
                return
            return
        with guard.telemetry_guard("_send_thinking_start"):
            available_tools=[
                tool_name
                for tool in tools or []
                if (tool_name := _tool_name_from_schema(tool)) is not None
            ]
            await self.observer.on_thinking_start(
                self.agent_name,
                operation_id= operation_id,
                initial_prompt= _build_thinking_prompt_snapshot(messages, tools),
                tools= available_tools or None,
                instance_id=invocation_id,
                operation_kind=operation_kind,
            )

    async def _send_thinking_end(
        self,
        operation_id:str,
        input_tokens:int,
        output_tokens:int,
        reasoning_tokens:int,
        tool_call_ids:list[str],
        reasoning_content:str|None=None,
        cached_tokens:int=0,
        model_id: str|None=None,
        operation_kind:str|None=None,
    )->None:
        if operation_kind in _INTERNAL_OPERATION_KINDS:
           return
        invocation_id = execution_context.get_current_invocation_id()
        if not invocation_id:
            if isinstance(self.observer, callbacks.NoOPRuntimeObserver):
                return
            return
        with guard.telemetry_guard("_send_thinking_end"):
            await self.observer.on_thinking_end(
                self.agent_name,
                operation_id= operation_id,
                input_tokens= input_tokens,
                output_tokens= output_tokens,
                reasoning_tokens= reasoning_tokens,
                tool_call_ids= tool_call_ids,
                reasoning_content= reasoning_content,
                cached_tokens= cached_tokens,
                instance_id=invocation_id,
                model_id= model_id or self.model_name,
                operation_kind=operation_kind,
            )
    @property
    def output_token_budget(self)->int:
        for option in (self._init_generate_kwargs,self.model_kwargs):
            value= option.get('max_tokens')
            if isinstance(value, int) and not isinstance(value, bool):
                return max(value,0)
        return 0

    def _get_provider_name(self)->str:
        return self.llm_provider or "unregistered-provider"

    async def _build_request_headers(
        self,
    )->dict[str,str]:

        final_headers=dict(self._default_headers)
        with guard.telemetry_guard("_build_request_headers:plugin_hook"):
            bridge= runtime_bindings.get_plugin_hook_bridge()
            if bridge is not None:
                hook_input = runtime_plugin_hooks.ChatHeadersInput(headers=dict(final_headers))
                hook_output = runtime_plugin_hooks.ChatHeadersOutput(headers=dict(final_headers))
                await bridge.run_chat_headers_hook(hook_input, hook_output)
                final_headers= dict(hook_output.headers)
        final_headers["Connection"]="close"
        return final_headers

    async def _prepare_provider_attempt(
        self,
        attempt_number: int,
    )->tuple[str,dict[str,str]]:
        auth= transport_contracts.AuthHookProjection()
        if self._auth_hook is not None:
            raw_auth = await self._auth_hook()
            if raw_auth is not None:
                auth = transport_contracts.AuthHookProjection.model_validate(raw_auth)
            self._auth_api_key = auth.api_key
            self._auth_headers = dict(auth.headers)
        resolved_model_id =self.model_name
        if self._model_identity_resolver is not None:
            resolved_model_id = await self._model_identity_resolver(
                self.model_name,
                auth.api_key or "",
                auth.account_fingerprint,
                auth.principal_revision,
                auth.principal_environment,
            )
        headers = await self._build_request_headers()
        headers.update(self._auth_headers)
        if self._model_identity_resolver is not None:
            headers["Model-Id"]=resolved_model_id
        if self._request_attestor is not None:
            headers= await provider_request_attestation.attest_and_merge_provider_request_headers(
                provider_id = self._get_provider_name(),
                model_id = resolved_model_id,
                base_url= self.base_url or "",
                auth_token= auth.api_key or "",
                attempt_number= attempt_number,
                headers = headers,
                attestor= self._request_attestor,
            )
        return resolved_model_id, headers

    def _record_tokens(
        self,
        input_tokens:int,
        output_tokens:int,
        reasoning_tokens:int=0,
        cached_tokens:int=0,
        operation_kind:str="normal",
    )->None:
        if self.token_counter and self.agent_name:
            with guard.telemetry_guard("_record_tokens"):
                # TokenCounter 目前只实现了 __init__，尚未提供任何 token 记录方法
                # （既没有 set_api_tokens(...)，也没有 MemoryTokenSink 协议里声明的
                # set_last_api_token(input_token=, output_tokens=)）。
                # 原调用每次都抛 AttributeError，被 telemetry_guard 吞掉后只留一条
                # ERROR 日志。token 统计属遥测旁路，这里统一做能力探测：接口存在
                # 才调用，否则安静跳过，不影响主链路。
                recorder = getattr(self.token_counter, "set_api_tokens", None)
                if recorder is not None:
                    recorder(
                        agent_name=self.agent_name,
                        prompt_tokens=input_tokens,
                        completed_tokens=output_tokens,
                        reasoning_tokens=reasoning_tokens,
                        cached_tokens=cached_tokens,
                        operation_kind=operation_kind,
                    )
                else:
                    fallback = getattr(self.token_counter, "set_last_api_token", None)
                    if fallback is not None:
                        fallback(
                            input_token=input_tokens,
                            output_tokens=output_tokens,
                        )

    async def _apply_chat_params_hook(
        self,
        kwargs: transport_contracts.ProviderOptions,
    )->transport_contracts.ProviderOptions:
        with guard.telemetry_guard("_apply_chat_params_hook"):
            bridge = runtime_bindings.get_plugin_hook_bridge()
            if bridge is None:
                return kwargs
            raw_generate_options = kwargs.get("generate_options")
            generate_options=(
                _JSON_OBJECT_ADAPTER.validate_python(raw_generate_options)
                if isinstance(raw_generate_options, dict)
                else {}
            )
            hook_input = runtime_plugin_hooks.ChatParamsInput.model_validate(
                {
                    "temperature": generate_options.get("temperature"),
                    "top_p": generate_options.get("top_p"),
                    "top_k": generate_options.get("top_k"),
                    "max_tokens": generate_options.get("max_tokens"),
                    "options":{
                        key:value
                        for key, value in generate_options.items()
                        if key not in ("temperature", "top_p", "top_k", "max_tokens")
                    },
                }
            )
            hook_output = runtime_plugin_hooks.ChatParamsOutput(
                temperature= hook_input.temperature,
                top_p=hook_input.top_p,
                top_k=hook_input.top_k,
                max_tokens=hook_input.max_tokens,
                options=dict(hook_input.options),
            )
            await bridge.run_chat_params_hook(hook_input, hook_output)
            generate_options =dict(generate_options)
            if hook_output.temperature is not None:
                generate_options["temperature"] = hook_output.temperature
            if hook_output.top_p is not None:
                generate_options["top_p"] = hook_output.top_p
            if hook_output.top_k is not None:
                generate_options["top_k"] = hook_output.top_k
            if hook_output.max_tokens is not None:
                generate_options["max_tokens"] = hook_output.max_tokens
            generate_options.update(hook_output.options)
            update_kwargs =dict(kwargs)
            update_kwargs["generate_kwargs"] = generate_options
            return update_kwargs


