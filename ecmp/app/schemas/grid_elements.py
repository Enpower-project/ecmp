from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID


class GridElementCreateAPI(BaseModel):
    parent_id: Optional[str] = None
    code: str
    type: str
    name: str
    voltage_level: str
    location: str


class GridElementCreateDB(BaseModel):
    parent_id: Optional[UUID] = None
    code: str
    type: str
    name: str
    voltage_level: str
    location: str


class GridElementData(BaseModel):
    grid_element_id: UUID = Field(...)
    parent_id: Optional[UUID] = None
    code: str
    type: str
    name: str
    voltage_level: str
    location: str
    created_at: datetime

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class GridElementResponse(BaseModel):
    success: bool = True
    data: GridElementData


class GridElementsListResponse(BaseModel):
    success: bool
    data: List[GridElementData]


class GridElementDeleteResponse(BaseModel):
    success: bool = True
    message: str



