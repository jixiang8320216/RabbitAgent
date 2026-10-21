

from interfaces.commands.protocol import CommandProtocol, CommandResult


class ExitCommand(CommandProtocol):
    name = "exit"
    description = "Exit the application"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(success=True, should_exit=True, message="Bye")
