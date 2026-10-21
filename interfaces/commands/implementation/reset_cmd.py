

from interfaces.commands.protocol import CommandProtocol, CommandResult


class ResetCommand(CommandProtocol):
    name = "reset"
    description = "Reset the current session"

    def __init__(self, context=None):
        self._context = context

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(success=True, message="Session reset")
