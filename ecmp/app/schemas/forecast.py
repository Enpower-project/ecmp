from uuid import UUID

from pydantic import BaseModel, Field


class ForecastParameters(BaseModel):
    start_datetime: str = Field(
        ...,
        description="String in a datetime format (e.g., 2025-03-26T23:00:00)",
    )
    end_datetime: str = Field(
        ...,
        description="String in a datetime format (e.g., 2025-03-26T23:00:00)",
    )
