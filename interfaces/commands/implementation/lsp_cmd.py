from interfaces.commands.protocol import CommandProtocol, CommandResult


class LSPCommand(CommandProtocol):
    name = "lsp"
    description = "Language Server operations"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/lsp 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “lsp: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )
