from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID
from typing import Literal


class ServicePointCreate(BaseModel):
    member_id: UUID = Field(...)
    code: str
    role: Optional[Literal["producer", "consumer", "prosumer"]] = None
    voltage_level: str
    address: Dict[str, Any]
    timezone: str
    active: bool
    grid_element_id: UUID = Field(...)


class ServicePointData(BaseModel):
    sp_id: UUID = Field(...)
    member_id: UUID = Field(...)
    code: str
    role: Optional[Literal["producer", "consumer", "prosumer"]] = None
    voltage_level: str
    address: Dict[str, Any]
    timezone: str
    active: bool
    grid_element_id: UUID = Field(...)

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class ServicePointResponse(BaseModel):
    success: bool = True
    data: ServicePointData


class ServicePointsListResponse(BaseModel):
    success: bool
    data: List[ServicePointData]



