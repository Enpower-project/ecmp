from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AllocationRecipientCreate(BaseModel):
    recipient_sp_id: UUID = Field(...)
    coefficient: float = Field(..., ge=0.0, le=1.0)


class ProducerAllocationConfigurationCreate(BaseModel):
    ec_id: UUID = Field(...)
    producer_sp_id: UUID = Field(...)
    valid_from: datetime
    valid_to: Optional[datetime] = None
    allocations: List[AllocationRecipientCreate]


class AllocationCoefficientData(BaseModel):
    id: UUID = Field(...)
    valid_from: datetime
    valid_to: Optional[datetime] = None
    coefficient: float

    model_config = {
        "from_attributes": True,
        "population_by_name": True,
    }


class AllocationLinkData(BaseModel):
    id: UUID = Field(...)
    ec_id: UUID = Field(...)
    producer_sp_id: UUID = Field(...)
    recipient_sp_id: UUID = Field(...)
    name: Optional[str] = None
    description: Optional[str] = None
    active: bool
    created_at: datetime
    coefficients: List[AllocationCoefficientData]

    model_config = {
        "from_attributes": True,
        "population_by_name": True,
    }


class ProducerAllocationConfigurationData(BaseModel):
    ec_id: UUID = Field(...)
    producer_sp_id: UUID = Field(...)
    valid_from: datetime
    valid_to: Optional[datetime] = None
    allocations: List[AllocationLinkData]


class ProducerAllocationConfigurationResponse(BaseModel):
    success: bool = True
    data: ProducerAllocationConfigurationData


class AllocationLinksListResponse(BaseModel):
    success: bool
    data: List[AllocationLinkData]
