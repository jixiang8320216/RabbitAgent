import pydantic
import enum


class PromptExpansionType(str,enum.Enum):
    SLASH_COMMAND = "slash_command"
    MCP_PROMPT = "mcp_prompt"

class PermissionMode(str,enum.Enum):
    DEFAULT="default"
    ACCEPT_EDITS="acceptEdits"
    PLAN="plan"
    BYPASS_PERMISSIONS="bypassPermissions"

class HookEffort(str,enum.Enum):
    LOW="low"
    MEDIUM="medium"
    HIGH="high"

class InstructionMemoryType(str,enum.Enum):
    USER="User"
    PROJECT="Project"
    LOCAL="Local"
    MANAGED="Managed"

class InstructionLoadReason(str,enum.Enum):
    SESSION_START="session_start"
    NESTED_TRAVERSAL="nested_traversal"
    PATH_GLOB_MATCH="path_glob_match"
    INCLUDE="include"
    COMPACT="compact"

class SessionEndReason(str,enum.Enum):

    CLEAR="clear"
    RESUME="resume"
    LGOOUT="lgoout"
    PROMPT_INPUT_EXIT="prompt_input_exit"
    BYPASS_PERMISSIONS_DISABLED="bypass_permissions_disabled"
    OTHER="other"