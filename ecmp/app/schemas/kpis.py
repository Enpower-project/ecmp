from pydantic import BaseModel, Field
from decimal import Decimal
from typing import List, Dict, Any
from datetime import datetime
from uuid import UUID


class BaselineRequest(BaseModel):
    start_datetime: str = Field(..., description="String in a datetime format (e.g., 2025-03-26T23:00:00)")
    end_datetime: str = Field(..., description="String in a datetime format (e.g., 2025-03-26T23:00:00)")


# class FlexibilityRequestCreate(BaseModel):
#     congestion_point: str
#     start: datetime
#     end: datetime
#     type: str
#     amount: Decimal
#     uom: str
#     price: Decimal
#     price_uom: str


# class FlexibilityRequestData(BaseModel):
#     id: UUID = Field(...)
#     congestion_point: str
#     start: datetime
#     end: datetime
#     type: str
#     amount: Decimal
#     uom: str
#     price: Decimal
#     price_uom: str
#     created_at: datetime

#     model_config = {
#         "from_attributes": True, #ORM compatibility
#         "population_by_name": True # support for aliases
#     }


# class FlexibilityRequestResponse(BaseModel):
#     success: bool = True
#     data: FlexibilityRequestData


# class FlexibilityRequestListResponse(BaseModel):
#     success: bool
#     data: List[FlexibilityRequestData]

