
import enum

class CheckpointReason(str,enum.Enum):

    MANUAL = "Manual"
    CODDE_WRITE = "code_write"
    CODE_UPDATE = "code_update"
    SHELL_COMMAND = "shell_command"