from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel


class OrgResponse(BaseModel):
    id: int
    name: str
    slug: str
    workable_connected: bool
    workable_subdomain: Optional[str] = None
    plan: str
    assessments_used: int
    assessments_limit: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: Optional[str] = None
    workable_config: Optional[Dict[str, Any]] = None


class WorkableConnect(BaseModel):
    code: str
