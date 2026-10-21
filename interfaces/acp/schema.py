
from pydantic import BaseModel,Field
from typing import Optional,Annotated,Dict,Any


class ModelInfo(BaseModel):
    model_config = {
        "populate_by_name": True,
    }

    field_meta:Annotated[
        Optional[Dict[str,Any]],
        Field(
            alias="_meta",
            description="The meta information of the model",
        ),
    ]=None
    description:Annotated[Optional[str],Field(description="Optional description of the model." )]= None
    model_id : Annotated[str,Field( alias="modelId",description="Unique identifier of the model." )]
    name:Annotated[str,Field(description="Unique identifier of the model." )]


class SessionMode(BaseModel):
    id: str
    name: str
    description: Optional[str] = None