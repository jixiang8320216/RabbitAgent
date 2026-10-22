import typing

from domains.llm.provider.domain import runtime_configuration
import collections.abc
import collections.abc as abc


class MultimodalConfigurationSource(typing.Protocol):

    def get_property(
        self,
        key:str ,
        default:object = None,
        experted_type: type[object] | None = None,
    )->object:
        """ Return one runtime configuration property """

type ProviderMultimodalProjector= collections.abc.Callable[
    [
        runtime_configuration.JsonObject,
        runtime_configuration.JsonObject,
        MultimodalConfigurationSource,
    ],
    runtime_configuration.JsonObject,
]