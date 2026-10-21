

from interfaces.commands.protocol import CommandProtocol, CommandResult

#todo 自动生成
class ShellCommand(CommandProtocol):
    name = "shell"
    description = "Execute a shell command"

    async def execute(self, args: list[str], context=None) -> CommandResult:
        return CommandResult(
            success=False,
            message=(
                "/shell 尚未实现：当前为占位实现，调用不会产生任何实际效果。"
                "原实现返回 success=True 但只输出 “shell: scaffold placeholder”，"
                "属于「谎报成功」——用户无法区分"
                "「已执行但无结果」与「根本没实现」。"
            ),
        )
