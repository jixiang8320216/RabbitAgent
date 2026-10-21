
import enum

class HookImportFormat(str, enum.Enum):
    CLAUDE = "clause"
    LEGACY_RABBIT = "legacy_rabbit"