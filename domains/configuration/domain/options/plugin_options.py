from domains.configuration.domain.model.config_data import RuntimeConfigModel
from pydantic import Field


class RuntimePluginsConfig(RuntimeConfigModel):
    disabled: list[str] = Field(default_factory=list)