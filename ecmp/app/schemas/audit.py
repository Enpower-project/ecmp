from pydantic import BaseModel, Field
from typing import List, Dict, Any

class LogEvent(BaseModel):
     timestamp: str
     request_id: str
     upstream_host: str
     requested_path: str
     request_method: str
     user_agent: str
     duration_ms: int = Field(..., ge=0, description="Duration in milliseconds", example=20)
     response_flags: str
     protocol: str
     request_scope: str
     jwt_subject: str
     response_code: int
     decision: str
     reason: str
	 

