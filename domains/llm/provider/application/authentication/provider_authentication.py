import collections.abc
import typing

class ProviderAuthResult(typing.TypedDict, total=False):

    api_key_id: str
    headers: dict[str, str]
    principal_environment: str
    principal_revision: int

# ``Awaitable`` 来自 ``collections.abc``；标准库 ``abc`` 模块没有这个属性，
# 写成 ``abc.Awaitable`` 会在导入期直接抛 AttributeError。
type ProviderAuthHook=collections.abc.Callable[[],collections.abc.Awaitable[ProviderAuthResult]]