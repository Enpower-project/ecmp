from pydantic import BaseModel, Field
from typing import List, Dict, Any


class StatusParameters(BaseModel):
    datetime: str = Field(..., description="String in a datetime format (e.g., 2025-03-26T23:00:00)")