from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID


class SubChannelCreate(BaseModel):
    submeter_id: UUID = Field(...)
    hrid: Optional[str] = None
    role: str
    uom: str
    interval_minutes: int
    scaling_factor: float = Field(default=1.0)


class SubChannelData(BaseModel):
    id: UUID = Field(...)
    submeter_id: UUID = Field(...)
    hrid: Optional[str] = None
    role: str
    uom: str
    interval_minutes: int
    scaling_factor: float

    model_config = {
        "from_attributes": True,
        "population_by_name": True
    }


class SubChannelResponse(BaseModel):
    success: bool = True
    data: SubChannelData


class SubChannelsListResponse(BaseModel):
    success: bool
    data: List[SubChannelData]
