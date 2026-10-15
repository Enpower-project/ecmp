from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID


class MeterCreate(BaseModel):
    sp_id: UUID = Field(...)
    serial_no: str
    model: str
    installed_at: datetime


class MeterData(BaseModel):
    meter_id: UUID = Field(...)
    sp_id: UUID = Field(...)
    serial_no: str
    model: str
    installed_at: datetime

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class MeterResponse(BaseModel):
    success: bool = True
    data: MeterData


class MetersListResponse(BaseModel):
    success: bool
    data: List[MeterData]
