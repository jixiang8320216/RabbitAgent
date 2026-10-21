"""Hook 运行时适配器。

把 MCP 管理器、模型配置适配成 hook 生命周期所需的客户端端口。

**已知缺口**（迁移遗留，非本次重组引入）：
``HookMcpClientAdapter`` / ``HookModelClientAdapter`` 的协议定义位于
``domains/hook/application/runtime_ports.py``，该文件当前为空壳（0 字节）。
调用方 ``rabbit_application_composition`` 在装配 hook 生命周期时会实例化这两个
适配器，因此该路径在运行时仍会抛 AttributeError。

本模块先固定其归属位置，待 hook 域补齐端口定义后即可直接启用。
"""

__all__ = ["HookMcpClientAdapter", "HookModelClientAdapter"]


class _MissingAdapter:
    """端口定义缺失时的显式占位。

    与静默返回 None 不同，占位会在实例化时给出可定位的报错，
    避免问题在更深的调用栈里以「NoneType has no attribute」的形式暴露。
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "Hook 客户端端口尚未定义："
            "domains/hook/application/runtime_ports.py 为空模块，"
            "缺少 HookMCPClient / HookModelClient 协议。"
        )


class HookMcpClientAdapter(_MissingAdapter):
    """MCP 客户端适配器。"""


class HookModelClientAdapter(_MissingAdapter):
    """模型客户端适配器。"""
