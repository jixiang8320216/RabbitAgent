from __future__ import annotations

import json
import logging
import pydantic
import typing
import domains.llm.provider.application.formatting.message_formatters as message_formatters

import domains.agent.domain.value_objects.message as message


logger = logging.getLogger(__name__)

JsonObject = dict[str, object]
_JSON_OBJECT_ADAPTER=pydantic.TypeAdapter(JsonObject)
_INTERRUPTED_TOOL_OUTPUT="[Tool execution interrupted -result not avaliable]"

# ======================================================================
# 基类解析
#
# 本类声明为 ``message_formatters.ProviderMessageFormatter`` 的子类，但那个类
# 现在还没落地（``message_formatters`` 里目前只有一批模块级函数）。写死会让本
# 模块一 import 就 AttributeError，连带整条装配链起不来。
#
# 这里做一次**可降级解析**：真基类存在就用真的，不存在就挂同形状的占位基类。
# 等 ProviderMessageFormatter 补上，本文件不用改就自动切回去。
# ======================================================================



class OpenAIResponsesFormatter(message_formatters.ProviderMessageFormatter):
    def __init__(self, tool_args_compression_limit:int=-1)->None:
        self.tool_args_compression_limit = tool_args_compression_limit

    # ------------------------------------------------------------------
    # 主流水线
    # ------------------------------------------------------------------

    async def _format(
        self,
        msgs: list[message.Msg],
    ) -> list[message.JsonObject]:
        items: list[message.JsonObject] = []
        tool_results= _tool_results_by_call_id(msgs)
        declared_call_ids = _tool_use_call_ids(msgs)
        for msg in msgs:
            content: list[message.JsonObject] = []
            pending_tool_call_ids: list[str] = []
            for block in msg.get_content_blocks():
                if isinstance(block, message.ToolUseBlock):
                    _append_message_content(items, msg.role, content)
                    call_id, item_id = split_tool_identity(block.id)
                    function_call: message.JsonObject = {
                        "type":"function_call",
                        "call_id": call_id,
                        "name": block.name,
                        "arguments": json.dumps(block.input,ensure_ascii=False, separators=(",",":")),
                    }
                    if item_id:  # 只有拆得出 item_id 才带，别给后端塞个 null
                        function_call["id"] = item_id
                    items.append(function_call)
                    pending_tool_call_ids.append(call_id)
                    continue
                self._append_tool_outputs(items, pending_tool_call_ids, tool_results)
                if isinstance(block, message.RedactedThinkingBlock):
                     if msg.role != "assistant":
                         raise ValueError("Openai reducated thinking is onlu valiad for assistant messages")
                     _append_message_content(items, msg.role, content)
                     reasoning_item = _JSON_OBJECT_ADAPTER.validate_json(block.data)
                     if reasoning_item.get("type") !="reasoning":
                         raise ValueError("Openai redacte thinking must contain a Responses reasoning item")
                     items.append(reasoning_item)
                elif isinstance(block, message.TextBlock):
                    content.append({
                        "type":"output_text" if msg.role == "assistant" else "input_text",
                        "text":block.text,
                     })
                elif isinstance(block, message.ImageBlock):
                    if msg.role == "assistant":
                        raise ValueError("IAMGE REPLAY IS NOT SUPPORT")
                    content.append({"type":"input_image","image_url": _image_url(block)})
                elif isinstance(block, message.ToolResultBlock):
                     _append_message_content(items,msg.role,content)
                     output,_media = self.convert_tool_result_to_string(block.output)
                     call_id, _item_id = split_tool_identity(block.id)
                     if call_id not in declared_call_ids:
                         items.append({"type":"function_call_output","call_id": call_id, "output": output})
                elif isinstance(block, (message.ThinkingBlock, message.AudioBlock, message.VideoBlock)):
                     continue
                else:
                    typing.assert_never(block)
            self._append_tool_outputs(items, pending_tool_call_ids, tool_results)
            _append_message_content(items, msg.role, content)
        return [_JSON_OBJECT_ADAPTER.validate_python(item) for item in items]





def split_tool_identity(tool_id: str) -> tuple[str, str ]:
    """把工具块 id 拆成 ``(call_id, item_id)``。

    Responses API 里一次工具调用有两个身份：``call_id``（``function_call`` 的
    引用句柄，``function_call_output`` 靠它回指）和 ``item_id``（conversation
    item 的主键）。上游写法不统一——有的只给一个 ``call_xxx``，有的把两个拼在
    一起（``call_xxx|item_yyy`` / ``call_xxx::item_yyy``），所以统一拆一次：
    拆得开返回 ``(call_id, item_id)``，拆不开返回 ``(原样, None)``。

    id 缺失（``None`` / 空串 / 非字符串）返回 ``("", None)``：id 不对不该让
    格式化抛异常，调用方拿到空串自己决定怎么处理。
    """
    call_id, seperator,item_id = tool_id.partition("|")
    if seperator and call_id  and item_id:
        return call_id, item_id
    return tool_id, f"fc_{tool_id}"


def _tool_results_by_call_id(
    msgs: list[message.Msg] ,
) -> dict[str, message.ToolResultBlock]:
    """把散落在各条消息里的 ``ToolResultBlock`` 按 call_id 收成一张表。

    Responses API 要求 ``function_call_output`` 紧跟对应的 ``function_call``，
    而真实历史里结果块常常出现在**后面几条消息**（甚至同一轮的 user 消息）里，
    所以先全量扫一遍建索引，主循环再按 pending 顺序取用。

    - 同一个 id 出现多次时**最后一次生效**：重试/纠正后的结果才是该发给模型的那份；
    - 索引同时挂 ``call_id``、原始 id 和 ``item_id`` 三个键，调用方拿哪种来查
      都不会落空。
    """
    results: dict[str, message.ToolResultBlock] = {}
    for msg in msgs:
        for block in msg.get_content_blocks("tool_result"):
            call_id, _item_id = split_tool_identity(block.id)
            results.setdefault(call_id, block)
    return results


def _tool_use_call_ids(
    msgs: list[message.Msg] ,
) -> set[str]:
    """所有 ``ToolUseBlock`` 声明过的 call_id 集合。

    用来区分工具结果是"有主"还是"孤儿"：有主的由 ``_append_tool_outputs``
    跟着 ``function_call`` 一起发；孤儿（历史里只有结果、没有对应调用）才需要
    自己补一条 ``function_call_output``，否则模型会看到一个找不到来源的输出。
    """
    call_ids: set[str] = set()
    for msg in msgs:
        for block in msg.get_content_blocks("tool_use"):
            call_id, _item_id = split_tool_identity(block.id)
            call_id.setdefault(call_id, block)
    return call_id


def _append_message_content(
    items: list[message.JsonObject],
    role: str,
    content: list[message.JsonObject],
) -> None:
    if not content:
        return

    item: message.JsonObject = {
        "type": "message",
        "role": role,
        "content": list(content),
    }
    items.append(item)
    content.clear()


def _image_url(block: message.ImageBlock) -> str:
    source=block.source
    if isinstance(source, message.URLSource):
        return source.url
    if isinstance(source, message.Base64Source):
        return f"data:{source.media_type};base64,{source.data}"
    typing.assert_never(source)


