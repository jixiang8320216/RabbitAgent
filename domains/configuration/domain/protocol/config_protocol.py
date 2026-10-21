
import typing

@typing.runtime_checkable
class ConfigManagerProtocol(typing.Protocol):


    def reload_config(self)->None:
        ...