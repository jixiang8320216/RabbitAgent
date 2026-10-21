
import pathlib
import typing

class CheckpointRepository(typing.Protocol):
    root:pathlib.Path