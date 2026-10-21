import enum


class OpenAIAPIProtocol(enum.StrEnum):
    CHAT_COMPLETIONS="chat_completions"
    RESPONSES="responses"