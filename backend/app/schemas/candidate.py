from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class CandidateResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    position: Optional[str] = None
    organization_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
