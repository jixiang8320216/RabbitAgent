

from interfaces.commands.protocol import CommandProtocol, CommandResult


class HelpCommand(CommandProtocol):
    name = "help"
    description = "Show available commands"

    def __init__(self, registry):
        self.registry = registry

    async def execute(self, args: list[str], context=None) -> CommandResult:
        names = sorted(getattr(self.registry, "_commands", {}).keys())
        return CommandResult(
            success=True,
            message="Available commands: " + (", ".join(names) if names else "(none)"),
        )
