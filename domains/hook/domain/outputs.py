


import enum

class HookRunStatus(str,enum.Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    ABANDONED = "abandoned"

class HookDecisionAction(str,enum.Enum):
    CONTINUE="continue"
    ALLOW="allow"
    ASK="ask"
    DEFER="defer"
    DENY="deny"
    BLOCK="block"
    ACCEPT="accept"
    DECLINE="decline"
    CANCEL="cancel"
    FAIL="fail"
