import typing
import abc

class ProviderAuthResult(typing.TypedDict, total=False):

    api_key_id: str
    headers: dict[str, str]
    principal_environment: str
    principal_revision: int

type ProviderAuthHook=abc.Callable[[],abc.Awaitable[ProviderAuthResult]]