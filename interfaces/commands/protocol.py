


from dataclasses import dataclass
from typing import Optional, runtime_checkable,Protocol,List


@dataclass
class CommandResult:

    success: bool
    should_exit: bool = False
    message: Optional[str] = None


@runtime_checkable
class CommandProtocol(Protocol):

    @property
    def name(self) -> str:

        ...

    @property
    def description(self) -> str:
        ...


    async def execute(self, args: List[str], context=None) -> CommandResult:
        """执行命令。

        :param args: 去掉命令名后的参数列表（例如 ``/config set model x`` -> ``["set","model","x"]``）。
        :param context: 运行时上下文（RuntimeContext / 会话状态等），可为 None。
        :return: :class:`CommandResult`，``should_exit=True`` 表示请求退出应用。
        """
        ...
