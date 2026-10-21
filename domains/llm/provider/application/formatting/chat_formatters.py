
import domains.llm.provider.application.formatting.message_formatters as message_formatters
import domains.llm.provider.application.formatting.tool_argument_formatter as tool_argument_formatter

class TruncatingOpenAIChatFormatter(message_formatters.OpenAIMessageFormatter):

    def __init__(self, tool_args_compression_limit=-1):
        super().__init__()
        self.tool_args_compression_limit = tool_args_compression_limit


    async def format(self, *args, **kwargs):

        formatted_messages = await super().format(*args, **kwargs)

        tool_argument_formatter.DynamicToolListFormatter._repair_formatted_tool_messages(formatted_messages)

        tool_argument_formatter.DynamicToolListFormatter._compress_all_tool_arguments(
            formatted_messages, max_bytes= self.tool_args_compression_limit
        )

        if args:
            tool_argument_formatter.DynamicToolListFormatter._inject_reasoning_content(args[0], formatted_messages)
        return formatted_messages

class KimitChatFormatter(tool_argument_formatter.DynamicToolListFormatter):


    async def format(self, msgs:list)->list:
        messages= await super()._format(msgs)
        tool_argument_formatter.DynamicToolListFormatter._repair_formatted_tool_messages(messages)
        for msg in messages:
            if isinstance(msg,dict) and msg.get("role")=="assistant":
                reasoning=msg.get("reasoning_content")
                if not reasoning:
                    msg["reasoning_content"]="(No reasoning content available)"
        return messages


class StringContentOpenAIFormatter(tool_argument_formatter.DynamicToolListFormatter):

    async def format(self, msgs:list)->list:
        messages= await super().format(msgs)
        self._flatten_messages(messages, remove_names=False)
        return messages


    async def format(self, *args, **kwargs) -> list:
        messages = await super().format(*args, **kwargs)
        self._flatten_messages(messages, remove_names=False)
        return messages

    @staticmethod
    def _flatten_messages(messages:list, *,remove_names=True)->True:
        for msg in messages:
            if isinstance(msg,dict):
                content=msg.get("content")
                if isinstance(content,list):
                    texts=[b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text"]
                    msg["content"]="\n".join(texts) if texts else ""
                elif content is None:
                    msg["content"]=""
                if remove_names:
                    msg.pop("name",None)

class DeepSeekReasoningFormatter(StringContentOpenAIFormatter):

    async def format(self,  *args, **kwargs)->list:
        messages = await super().format(*args, **kwargs)
        for msg in messages:
            if isinstance(msg,dict) and msg.get("role")=="assistant":
                reasoning=msg.get("reasoning_content")
                if not isinstance(reasoning,str) or not reasoning.strip():
                    msg["reasoning_content"]="(No reasoning content available)"

        return messages















