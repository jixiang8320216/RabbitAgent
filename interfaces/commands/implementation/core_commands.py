

from interfaces.commands.protocol import CommandProtocol, CommandResult


class ClearCommand(CommandProtocol):
    name = "clear"
    description = "Clear the screen"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(success=True, message="\x1b[2J\x1b[H")


class MultilineCommand(CommandProtocol):
    name = "multiline"
    description = "Toggle multiline input mode"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/multiline 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “multiline: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )


class TokensCommand(CommandProtocol):
    name = "tokens"
    description = "Show token usage"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/tokens 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “tokens: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )


class WebCommand(CommandProtocol):
    name = "web"
    description = "Web search"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/web 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “web: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )


class InterruptCommand(CommandProtocol):
    name = "interrupt"
    description = "Interrupt the current operation"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/interrupt 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “interrupt: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )
