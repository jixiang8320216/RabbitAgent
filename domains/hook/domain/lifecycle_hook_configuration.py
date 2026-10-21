import enum

class HookEvent(enum.StrEnum):
    PRE_REPLY = "pre_reply"
    POST_REPLY = "post_reply"
    PRE_TOOL_USE ="pre_tool_use"
    POST_TOOL_USE ="post_tool_use"
    PRE_PRINT = "pre_print"
    POST_PRINT = "post_print"
    PRE_REASONING = "pre_reasoning"
    POST_REASONING = "post_reasoning"
