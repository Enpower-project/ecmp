from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID
from typing import Literal


class CommunityData(BaseModel):
    id: UUID = Field(..., alias="ec_id")
    code: str
    type: Optional[Literal["CSC_00"]] = None
    name: str
    description: str
    created_at: datetime
    active: bool

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class CommunityMembershipData(BaseModel):
    ec_id: UUID = Field(...)
    member_id: UUID = Field(...)
    role: str
    joined_at: datetime
    left_at: Optional[datetime] = None

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class CommunityResponse(BaseModel):
    success: bool = True
    data: CommunityData


class CommunityListResponse(BaseModel):
    success: bool
    data: List[CommunityData]


