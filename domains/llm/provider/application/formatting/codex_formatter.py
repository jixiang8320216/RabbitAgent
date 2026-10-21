import domains.llm.provider.application.formatting.responses_formatter as response_formatter


class OpenAICodexResponsesFormatter(response_formatter.OpenAIResponsesFormatter):
    ...

split_tool_identity= response_formatter.split_tool_identity