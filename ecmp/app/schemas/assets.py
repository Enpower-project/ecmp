from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID


class AssetCreate(BaseModel):
    sp_id: UUID = Field(...)
    hrid: Optional[str] = None
    type: str
    installed_power: Optional[float] = None
    uom: Optional[str] = None
    description: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None


class AssetData(BaseModel):
    id: UUID = Field(...)
    sp_id: UUID = Field(...)
    hrid: Optional[str] = None
    type: str
    installed_power: Optional[float] = None
    uom: Optional[str] = None
    description: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None
    installed_at: datetime

    model_config = {
        "from_attributes": True,
        "population_by_name": True
    }


class AssetResponse(BaseModel):
    success: bool = True
    data: AssetData


class AssetsListResponse(BaseModel):
    success: bool
    data: List[AssetData]


class AssetSetLevel(BaseModel):
    asset_id: UUID = Field(...)
    key: str
    value: float
    unit: str



