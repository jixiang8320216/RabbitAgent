from collections.abc import Mapping

from pydantic import JsonValue, TypeAdapter, BaseModel, ConfigDict,Field

type ConfigData = dict[str,JsonValue]
type ConfigMapping = dict[str,JsonValue]
type  JsonObject =ConfigData

_CONFIG_DATA_ADAPTER=TypeAdapter(ConfigData)

def parse_config_data(value:object) -> ConfigData:
    if not isinstance(value, Mapping):
        return {}
    string_keyed = {
        str(key): item
        for key, item in value.items() if isinstance(key, str)
    }
    return _CONFIG_DATA_ADAPTER.validate_python(string_keyed)


class RuntimeConfigModel(BaseModel):
    model_config= ConfigDict(extra="allow",frozen=True, populate_by_name=True)
    __pydantic_extra__: dict[str,JsonValue]=Field(init=False)

    def to_config_data(self,*,exclude:set[str]|None=None) -> ConfigData:
        return _CONFIG_DATA_ADAPTER.validate_python(
            self.model_dump(mode="json", by_alias=True, exclude=exclude or set(), exclude_unset=True)
        )

