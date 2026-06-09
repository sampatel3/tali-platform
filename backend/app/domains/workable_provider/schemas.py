"""Workable-shaped request/response schemas for the Assessments-Provider API.

These match Workable's published Assessments Provider contract (not Taali's
internal shapes), so the integration speaks Workable's language at the edge.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr


class WorkableCandidate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    phone: Optional[str] = None


class CreateAssessmentRequest(BaseModel):
    test_id: str
    callback_url: str
    candidate: WorkableCandidate
    job_shortcode: Optional[str] = None
    job_title: Optional[str] = None


class ProviderTest(BaseModel):
    id: str
    name: str


class ProviderTestList(BaseModel):
    tests: list[ProviderTest]


class SharedLinkResponse(BaseModel):
    url: str
    ttl: str
    ttl_units: str
