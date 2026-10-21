import pathlib
import typing
import domains.configuration.domain.runtime_state.app_state as app_state_module

class SessionAppService:
    _legacy_session_migrator: typing.Callable[[str, pathlib.Path|None], app_state_module.JsonObject|None]|None =(
        None
    )

    @classmethod
    def set_legacy_session_migrator(
        cls,
        migrator: typing.Callable[[str, pathlib.Path|None], app_state_module.JsonObject|None]|None,
    )->None:
        cls._legacy_session_migrator = migrator