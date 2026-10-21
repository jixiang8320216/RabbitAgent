from interfaces.commands.protocol import CommandProtocol, CommandResult


class StatusCommand(CommandProtocol):
    name = "status"
    description = "Show runtime status"

    def __init__(self, context=None):
        self._context = context

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(success=True, message="Status: ok")
