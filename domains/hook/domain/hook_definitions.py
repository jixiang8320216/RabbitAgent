

import enum

class HookScope(str,enum.Enum):
    MANAGED = "managed"
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"
    PLUGIN = "plugin"
    SKILL = "skill"
    AGENT = "agent"
    SESSION = "session"


class HookSourceKind(str,enum.Enum):
    MANAGED = "managed"
    SYSTEM = "system"
    RABBIT = "rabbit"
    CLAUDE= "claude"
    PLUGIN = "plugin"
    SKILL = "skill"
    AGENT = "agent"
    SESSION = "session"

class HookEvent(str,enum.Enum):
    SESSION_START = "sessionStart"
    SETUP = "setup"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    USER_PROMPT_EXPANSION = "UserPromptExpansion"
    PRE_TOOL_USE="PreToolUse"
    PERMISSION_REQUEST="PermissionRequest"
    PERMISSION_DENIED="PermissionDenied"
    POST_TOOL_USE="PostToolUse"
    POST_TOOL_USE_FAILURE="PostToolUseFailure"
    POST_TOOL_BATCH = "PostToolBatch"
    NOTIFICATION="Notification"
    MESSAGE_DISPLAY="MessageDisplay"
    SUBAGENT_START="SubagentStart"
    SUBAGENT_STOP="SubagentStop"
    TASK_CREATED="TaskCreated"
    TASK_COMPLETED="TaskCompleted"
    STOP="Stop"
    STOP_FAILURE="StopFailure"
    TEAMMATE_IDLE="TeammateIdle"
    INSTRUCTIONS_LOADED="InstrumentsLoaded"
    CONFIG_CHANGED="ConfigChanged"
    CWD_CHANGED="CwdChanged"
    FILE_CHANGED="FileChanged"
    WORKTREE_CREATE="WorkTreeCreate"
    WORKTREE_REMOVE="WorkTreeRemove"
    PRE_COMPACT="PreCompact"
    POST_COMPACT="PostCompact"
    ELICITATION="Elicitation"
    ELICITATION_RESULT="ElicitationResult"
    SESSION_END="SessionEnd"





















