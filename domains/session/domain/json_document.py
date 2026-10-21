
from pydantic import JsonValue, TypeAdapter
type JsonObject = dict[str,JsonValue]


_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)

def parse_json_object(value:object)->JsonObject:
    return _JSON_OBJECT_ADAPTER.validate_python(value)

def parse_json_object_json(document:str)->JsonObject:
    return _JSON_OBJECT_ADAPTER.validate_json(document)