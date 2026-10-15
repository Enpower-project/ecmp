from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID


class PriceZoneCreate(BaseModel):
    zone_id: Optional[str] = None
    name: str
    currency: str


class PriceZoneData(BaseModel):
    zone_id: UUID = Field(...)
    name: str
    currency: str

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class PriceZoneResponse(BaseModel):
    success: bool = True
    data: PriceZoneData


class PriceZoneListResponse(BaseModel):
    success: bool
    data: List[PriceZoneData]



