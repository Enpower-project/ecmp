from pydantic import BaseModel, Field


class SimulationParameters(BaseModel):
    start_datetime: str = Field(..., description="String in a datetime format (e.g., 2025-03-26T23:00:00)")
    end_datetime: str = Field(..., description="String in a datetime format (e.g., 2025-03-26T23:00:00)")
    optimization_strategy: str = Field(..., description="String with a name of optimization strategy (no-optimization, self-consumption, collective-self-consumption)")
    battery_discharge_scope: str = Field(
        "local",
        description="Battery discharge scope for self-consumption optimization: local or community.",
    )
