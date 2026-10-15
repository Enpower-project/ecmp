from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID




class MemberCreateAPI(BaseModel):
    name: str
    first_name: str
    last_name: str
    email: str
    type: str #'prosumer','consumer','producer'
    credentials: Dict[str, Any]


class MemberCreateDB(BaseModel):
    external_ref: UUID
    name: str
    first_name: str
    last_name: str
    email: str
    type: str


class MemberData(BaseModel):
    id: UUID = Field(..., alias="member_id")
    external_ref: UUID = Field(...)
    name: str
    first_name: str
    last_name: str
    email: str
    type: str
    created_at: datetime

    model_config = {
        "from_attributes": True, #ORM compatibility
        "population_by_name": True # support for aliases
    }


class MemberResponse(BaseModel):
    success: bool = True
    data: MemberData


class MemberListResponse(BaseModel):
    success: bool
    data: List[MemberData]


