from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class OrgResponse(BaseModel):
    id: int
    name: str
    slug: str
    workable_connected: bool
    workable_subdomain: Optional[str] = None
    plan: str
    assessments_used: int
    assessments_limit: Optional[int] = None
    allowed_email_domains: List[str] = Field(default_factory=list)
    sso_enforced: bool = False
    saml_enabled: bool = False
    saml_metadata_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: Optional[str] = None
    workable_config: Optional[Dict[str, Any]] = None
    allowed_email_domains: Optional[List[str]] = None
    sso_enforced: Optional[bool] = None
    saml_enabled: Optional[bool] = None
    saml_metadata_url: Optional[str] = None


class WorkableConnect(BaseModel):
    code: str
