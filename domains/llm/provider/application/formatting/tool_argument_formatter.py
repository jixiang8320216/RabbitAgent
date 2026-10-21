from __future__ import annotations

import json
import logging
import re


import domains.llm.provider.application.formatting.message_formatters as message_formatters
import domains.agent.domain.aggregates.runtime_ports as runtime_ports
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.agent.domain.value_objects.plugin_hooks as plugin_hooks
import domains.llm.provider.application.capability.thinking_input as thinking_input_projection
import domains.agent.domain.value_objects.message as message_module
import domains.llm.provider.application.formatting.tool_message_pipeline as formatted_tool_message_pipeline

logger = logging.getLogger(__name__)

type JsonObject = dict[str, object]


class DynamicToolListFormatter(message_formatters.OpenAIMessageFormatter):
    """「动态工具列表」版请求格式化器。

    与普通格式化器的差别在**工具列表是动态的**：工具集每轮都可能变（子
    agent 注册 / 工具箱热插拔），所以本类把最近一次见到的工具声明存在
    ``self.tools`` 上，供 :meth:`_infer_tool_name_from_args` 反查——GLM 系
    模型经常只吐参数不吐工具名，只能靠参数键去匹配 schema。

    主入口是 :meth:`format`（``async``，因为 ``ReActAgent`` 是
    ``await formatter.format(msgs=[...])`` 这样调的），真正的同步逻辑在
    :meth:`_format` 里。

    流水线（任一步抛错都只记日志，不让格式化把一轮对话弄挂）::

        归一化 -> 抽思考 -> 注入 reasoning_content
              -> 补 system_prompt -> 洗 GLM 标记 -> 补工具名 -> 压参数
    """

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    def __init__(
        self,
        tool_args_compression_limit: int = -1,
    ) -> None:
        # 基类可能是占位基类（收任意参数），也可能是真基类（有必填参数），
        # 所以按形状试：先带全部关键字，不行就退化成无参。
        super().__init__()
        self.tool_args_compression_limit:int =tool_args_compression_limit
        self.agent_slot : runtime_ports.PluginHookSlotProtocol |None=None
        self._cached_thinking_input:dict={}


    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    async def format(
        self,
        *args,
        **kwargs: object,
    ):
        formatted_messages = await super().format(*args, **kwargs)
        self._compress_all_tool_arguments(formatted_messages, max_bytes= self.tool_args_compression_limit)
        # 原调用写作 _fix_glm_markers_in_formatted_messages，但类里并不存在该方法，
        # 只有 _fix_glm_markers_in_formatted_tool_messages。任何走本 format() 的模型
        # （如 DeepSeek：模型名含 "deepseek" 会命中 DeepSeekReasoningFormatter）都会
        # 在这里抛
        # AttributeError: 'DeepSeekReasoningFormatter' object has no attribute
        # '_fix_glm_markers_in_formatted_messages'，导致整条 turn 失败。
        # pipeline.fix_glm_markers_in_messages 遍历的正是全部消息里的 assistant
        # 工具调用，并不局限于 tool 角色消息，因此调用该方法即为原意。
        self._fix_glm_markers_in_formatted_tool_messages(formatted_messages)
        self._repair_formatted_tool_messages(formatted_messages)
        self._cached_thinking_input = self._extract_thinking_input(formatted_messages)
        execution_context.set_cached_thinking_input(self._cached_thinking_input)
        try:
            bridge= runtime_bindings.get_plugin_hook_bridge()
            # PluginHookBridgeAdapter 目前只提供通用入口
            # plugin_hooks_for_event()/has_plugins()，并没有实现
            # run_messages_transform_hook(...)。原代码直接调用它，每次都抛
            # "'PluginHookBridgeAdapter' object has no attribute
            # 'run_messages_transform_hook'"。这里做能力探测：桥接实现了该 hook
            # 才走消息变换，否则安静跳过（插件 hook 属旁路扩展点）。
            transform_hook = getattr(bridge, "run_messages_transform_hook", None) if bridge is not None else None
            if transform_hook is not None:
                _msgs_in = plugin_hooks.MessageTransformInput(messages= formatted_messages)
                _msgs_out = plugin_hooks.MessageTransformOutput(messages= formatted_messages)
                await transform_hook(
                    _msgs_in,
                    _msgs_out,
                    slot=self.agent_slot,
                )
                formatted_messages = _msgs_out.messages
        except Exception as e:
            logger.error(e)
        return  formatted_messages


    # ------------------------------------------------------------------
    # 思考内容
    # ------------------------------------------------------------------

    def _extract_thinking_input(self, formatted_messages: list) -> message_module.JsonObject:
        # 注意：as_formatted_message 定义在 formatted_tool_message_pipeline；
        # thinking_input_projection 里只有名字不同的 as_formatted_messages（复数）。
        # 原写法 thinking_input_projection.as_formatted_message 会抛
        # "module 'llm.provider.application.thinking_input_projection' has no
        # attribute 'as_formatted_message'"，使整条 turn 失败。
        return thinking_input_projection.project_thinking_input(
            formatted_tool_message_pipeline.as_formatted_message(formatted_messages)
        )

    @staticmethod
    def _inject_reasoning_content(original_msgs, formatted_messages):
        assistant_reasoning =[]
        for msg in original_msgs:
            if isinstance(msg, message_module.Msg) and msg.role =="assistant":
                reasoning_text =None
                has_surviving_content=False
                try:
                    for block in msg.get_content_blocks():
                        if isinstance(block, message_module.ThinkingBlock):
                            if reasoning_text is None:
                                reasoning_text = block.get("thinking","")or None
                            elif isinstance(block,(message_module.TextBlock,
                                                   message_module.ImageBlock,
                                                   message_module.ToolUseBlock,
                                                   message_module.AudioBlock,
                                )):
                                has_surviving_content = True

                except Exception:
                    pass
                if has_surviving_content:
                    assistant_reasoning.append(reasoning_text)
            idx=0
            injected_count=0
            for msg_dict in formatted_messages:
                if isinstance(msg_dict, dict) and msg_dict.get("role")=="assistant":
                    if idx < len(assistant_reasoning) and assistant_reasoning[idx]:
                        msg_dict["reasoning_content"] = assistant_reasoning[idx]
                        injected_count+=1
                        reasoning_preview=(
                            assistant_reasoning[idx][:200]+"..."
                            if len(assistant_reasoning[idx])>200
                            else assistant_reasoning[idx]
                        )
                    idx+=1


    async def _format(
        self,
        msgs: list,
    ) -> list:
       messages = await super()._format(msgs)
       self._inject_reasoning_content(msgs, messages)
       return messages

    @staticmethod
    def _compress_all_tool_arguments(
        messages: list,
        max_bytes: int=1000,
    ) -> None:
        formatted_tool_message_pipeline._compress_tool_arguments(
            formatted_tool_message_pipeline.as_formatted_message(messages),
            max_bytes= max_bytes,
            fix_markers = DynamicToolListFormatter._fix_glm_json_markers,
            _compress_arguments =DynamicToolListFormatter._compress_arguments,
        )

    def _fix_glm_markers_in_formatted_tool_messages(
        self,
        messages: list,
    ) ->int:
        return  formatted_tool_message_pipeline.fix_glm_markers_in_messages(
            formatted_tool_message_pipeline.as_formatted_message(messages),
            fix_markers = DynamicToolListFormatter._fix_glm_json_markers,
        )

    @staticmethod
    def _repair_formatted_tool_messages(
        messages: list,
    )->int:
        return formatted_tool_message_pipeline.repair_formatted_tool_messages(
            formatted_tool_message_pipeline.as_formatted_message(messages),
            infer_tool_name = DynamicToolListFormatter._infer_tool_name_from_args,
        )

    @staticmethod
    def _fix_glm_json_markers( args_str: str) -> str:
        """把一段夹带 GLM 标记的"参数"洗成能解析的 JSON 字符串。

        依次做：去 ``<|…|>`` 标记 → 去 ``functions.name:0`` 前缀 → 去代码
        围栏 → 去空白 → 尝试整体解析 → 不行就截取第一段配平的 JSON。

        最后一步**即使解析不出来也返回截取结果**：对参数而言，一段残缺的
        JSON 至少还有机会被上游再救一次，带着标记的原串则完全没有机会。

        :return: 清洗后的字符串；入参不是字符串/容器时返回 ``""``。
        """
        if not  isinstance(args_str, str):
            return args_str
        if "<arg_value>" not in args_str and "<arg_key>" not in args_str and "</arg_value>" not in args_str:
            return args_str

        result= args_str

        result = result.replace("\n\n<arg_value>",'": "')

        result = result.replace("</arg_value><arg_key>",'", "')

        result = result.replace("<arg_value>", '": "')

        result = result.replace("</arg_value>", '"')

        result = re.sub(r'"<arg_key>([^"]+)"',r'"\1', result)

        result = re.sub(r'"<arg_key>([^"]+)"', r'{"\1', result)

        result = re.sub(r',\s*<arg_key>([^"]+)"', r',"\1"', result)

        result = re.sub(r'(?<!{)<arg_key>"', ',"', result)

        result = re.sub(r'{\s*<arg_key>"', '{"', result)
        return result

    # ------------------------------------------------------------------
    # 工具名反查
    # ------------------------------------------------------------------
    @staticmethod
    def _infer_tool_name_from_args(args_str:str,) -> str:
        try:
            fixed_args_str = DynamicToolListFormatter._fix_glm_json_markers(args_str)
            args = json.loads(fixed_args_str) if isinstance(fixed_args_str, str) else fixed_args_str
            if isinstance(args, dict):
                if "command" in args:
                    return "execute_command"
                elif "file_path" in args and "code_content" in args:
                    return "write"
                elif "file_path" in args and "old_string" in args:
                    return "update"
                elif "file_path" in args and "start_line" in args:
                    return "read"
                elif "response" in args:
                    return "generate_response"
                elif "query" in args or "search" in args:
                    return "search"
                elif "file_path" in args:
                    return "read"
        except Exception as e:
            logger.warning(e)
        return "unknown_tool"


    def _compress_arguments(self,
        args_obj: message_module.JsonObject,
        tool_name:str,
        max_bytes:int,
    ) -> message_module.JsonObject:
        compressed : message_module.JsonObject = {}

        preserve_keys=["name", "description","expected_outcome","file_path","query"]

        for key,value in args_obj.items():
            if key.startswith("_"):
                continue
            if key in preserve_keys:
                if isinstance(value, (str,int,float, bool)) or value is None:
                    compressed[key] = value
                else:
                    compressed[key] = f"[COMPRESSED:{type(value).__name__}]"
            elif isinstance(value, list):
                if len(value) >0:
                    compressed[key] = f"[COMPRESSED:{len(value)} items]"
                else:
                    compressed[key] = []
            elif isinstance(value, dict):
                compressed[key] = f"[COMPRESSED:{len(value)} fields]"
            else:
                compressed[key] = value
        return compressed

