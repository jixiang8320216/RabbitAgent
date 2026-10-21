import typing

import pydantic

class StructuredToolResultModel(pydantic.BaseModel):
    ...

class ReadStructuredResult(StructuredToolResultModel):
    ...

class GlobFilesStructuredResult(StructuredToolResultModel):
    ...

class GrepContentStructuredResult(StructuredToolResultModel):
    ...

StructuredToolResult= typing.Annotated[
    ReadStructuredResult |GlobFilesStructuredResult | GrepContentStructuredResult,
    pydantic.Field(discriminator="kind")
]