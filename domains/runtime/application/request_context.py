
import pathlib
import typing

import domains.agent.domain.aggregates.execution_context as execution_context
import domains.plugin.application.request_context as plugin_request_context


def bind_app_request_context(
    app_id:str,
    project_home:str|pathlib.Path |None,
    app_instance: object |None= None,
    *,
    disabled_plugins: typing.Iterable[str] |None = None,
)->None:
    execution_context.bind_execution_context(app_id, project_home, app_instance)
    if disabled_plugins is not None:
        plugin_request_context.set_request_disbled_plugins(disabled_plugins)