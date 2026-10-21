"""domains.permission 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

from __future__ import annotations
from domains.permission.cli_strategy import CLIConfirmationStrategy
from domains.permission.interrupt_manager import InterruptManager
from domains.permission.application.confirmation_registry import  ConfirmationRegistry
from domains.permission.application.interrupt_coordination import InterruptManager
from domains.permission.domain.confirmation_strategy import ConfirmationStrategy
import typing


"""权限（permission）包。

对外暴露权限裁决相关的协议与内置实现：

- :class:`PermissionResolver`：权限裁决协议，供应用装配层以依赖注入方式持有。
- :class:`AllowAllPermissionResolver`：全放行的默认实现，用于尚未接入真实
  权限策略的运行形态（CLI / 本地开发）。

真实策略未就绪时采用「全放行」是刻意的降级选择：应用装配不应因为权限模块
缺失而中断启动，因此这里不抛异常，只提供最宽松的裁决结果。
"""


class PermissionDecision(typing.NamedTuple):
    """一次权限裁决的结果。

    :param allowed: 是否允许本次操作。
    :param reason: 裁决理由，便于审计与调试；允许时通常为空字符串。
    """

    allowed: bool
    reason: str = ""


class PermissionResolver(typing.Protocol):
    """权限裁决协议。

    调用方只依赖 ``resolve`` 一个方法，因此这里保持最小契约：任何持有
    ``resolve(request) -> bool`` 语义的对象都可以作为实现注入。

    为了同时兼容「返回布尔值」和「返回裁决对象」两种实现风格，协议的方法
    签名使用宽松的返回类型，由调用方自行判断。
    """

    def resolve(self, request: typing.Any = None, /, **kwargs: typing.Any) -> typing.Any:
        """对一次操作请求给出裁决。"""
        ...


class AllowAllPermissionResolver:
    """全放行的权限裁决实现。

    始终返回 ``True``，不读取任何外部策略文件，也不产生副作用。用于没有
    配置真实权限策略的场景，保证调用链可以继续执行。
    """

    __slots__ = ()

    def resolve(self, request: typing.Any = None, /, **kwargs: typing.Any) -> bool:
        """无论请求内容如何，一律放行。"""
        return True

    # 常见别名，便于调用点用不同命名访问同一语义。
    def check(self, request: typing.Any = None, /, **kwargs: typing.Any) -> bool:
        """``resolve`` 的别名。"""
        return True

    def is_allowed(self, request: typing.Any = None, /, **kwargs: typing.Any) -> bool:
        """``resolve`` 的别名。"""
        return True

    def __repr__(self) -> str:
        return "AllowAllPermissionResolver()"
