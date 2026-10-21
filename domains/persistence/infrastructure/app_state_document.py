import pydantic

from domains.configuration import JsonObject

_JSON_OBJECT_ADAPTER = pydantic.TypeAdapter(
    JsonObject,
    config=pydantic.ConfigDict(strict=True,allow_inf_nan=False)
)

_RETIRED_DOCUMENT_FIELDS=frozenset({"plan_notebook"})

class AppStateDocument(pydantic.BaseModel):
    ...

def normalize_legacy_document_json(payload: dict) -> AppStateDocument:
    document= _JSON_OBJECT_ADAPTER.validate_json(payload)
    for field_name in _RETIRED_DOCUMENT_FIELDS:
        document.pop(field_name, None)
    return AppStateDocument.model_validate(document)