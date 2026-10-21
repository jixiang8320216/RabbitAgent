import collections
import logging
import os
import pydantic
import domains.configuration.domain.model.app_mode as app_mode_configuration
import domains.agent.domain.aggregates.execution_context as  request_execution_context

APP_MODE_ENV_VAR = "RABBIT_APP_MODE"
APP_MODE_CONFIG_KEY = "app_mode"
DEFAULT_APP_MODE = "delegate"

_CONFIG_ADAPTER=pydantic.TypeAdapter(dict[str,object])
VALID_APP_MODES=tuple(sorted(app_mode_configuration.BUILTIN_APP_MODES))
logger = logging.getLogger(__name__)

# 从配置文件和环境变量中解析出应用启动模式，返回规范化的mode字符串
def load_app_mode(
    config_data: collections.abc.Mapping[str,object]|None = None,
)->app_mode_configuration.AppMode:
    source_config = config_data if config_data is not None else _load_user_config()
    config_appmode = _read_optional_text(source_config,  APP_MODE_CONFIG_KEY)
    environ_appmode = os.environ.get(APP_MODE_ENV_VAR)
    return resolver_app_mode(config_appmode,environ_appmode)

def resolver_app_mode(
    config_appmode: str|None,
    environ_appmode: str|None,
) -> str:
    """按「配置文件 > 环境变量 > 默认值」的顺序裁决应用模式。

    返回的永远是校验通过的模式名，因此调用方无需再处理 ``None``。
    """
    for app_mode in (config_appmode,environ_appmode):
        if app_mode is None:
            continue
        validated = validate_app_mode(app_mode)
        if validated is None:
            logger.warning("[resolver_app_mode]invalid app_mode %r, skipped", app_mode)
            continue
        return validated
    logger.debug("[resolver_app_mode]no app_mode configured, falling back to %r", DEFAULT_APP_MODE)
    return DEFAULT_APP_MODE

def validate_app_mode(value: str)->app_mode_configuration.AppMode|None:
    normalized = value.strip().lower()
    if normalized in VALID_APP_MODES:
        logger.debug("[validate_app_mode]loaded app_mode %s",normalized)
        return normalized
    return None

def _load_user_config()->dict[str,object]:
    return _CONFIG_ADAPTER.validate_python(request_execution_context.get_effective_runtime_config())

def _read_optional_text(config_data: collections.abc.Mapping[str,object], key:str)->str|None:
    value = config_data.get(key)
    if value is None:
        return value
    if isinstance(value, str):
        return value
    return None

