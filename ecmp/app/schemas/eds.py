from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID


class EDSReportRequest(BaseModel):
    datetime: str = Field(..., description="String in a datetime format (e.g., 2025-03-26T23:00:00)")


