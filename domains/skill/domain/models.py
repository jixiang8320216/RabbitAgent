
from pydantic import BaseModel,ConfigDict

_MODEL_CONFIG=ConfigDict(extra="forbid")

class SKillEvolutionModel(BaseModel):
     model_config = _MODEL_CONFIG


class BackgroundReviewConfig(SKillEvolutionModel):
    def __init__(self):
        pass