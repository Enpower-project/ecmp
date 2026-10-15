from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID


class ChannelCreate(BaseModel):
    meter_id: UUID = Field(...)
    role: str
    uom: str
    interval_minutes: int
    scaling_factor: float = Field(default=1.0)


class ChannelData(BaseModel):
    channel_id: UUID = Field(...)
    meter_id: UUID = Field(...)
    role: str
    uom: str
    interval_minutes: int
    scaling_factor: float

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class ChannelResponse(BaseModel):
    success: bool = True
    data: ChannelData


class ChannelsListResponse(BaseModel):
    success: bool
    data: List[ChannelData]
