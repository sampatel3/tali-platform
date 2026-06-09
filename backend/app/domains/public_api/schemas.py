"""Frozen response/request schemas for the public API.

These are intentionally separate from the internal serializers (e.g.
``AssessmentResponse``, ``ApplicationResponse``). The internal ones change
shape whenever the app needs them to; these are a contract we commit to.
Additive changes only within v1 — breaking changes go to ``/public/v2``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PublicTest(BaseModel):
    # Stable ``task_key`` where present, else the numeric id as a string.
    id: str
    name: str
    role: Optional[str] = None
    duration_minutes: Optional[int] = None


class PublicTestList(BaseModel):
    tests: list[PublicTest]


class PublicTaskSummary(BaseModel):
    id: int
    task_key: Optional[str] = None
    name: str


class PublicRole(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    source: Optional[str] = None
    workable_job_id: Optional[str] = None
    created_at: Optional[datetime] = None
    tasks: list[PublicTaskSummary] = Field(default_factory=list)


class PublicRoleList(BaseModel):
    roles: list[PublicRole]


class PublicCandidate(BaseModel):
    id: int
    full_name: Optional[str] = None
    email: Optional[str] = None


class PublicApplication(BaseModel):
    id: int
    status: Optional[str] = None
    pipeline_stage: Optional[str] = None
    application_outcome: Optional[str] = None
    candidate: Optional[PublicCandidate] = None
    role_id: Optional[int] = None
    role_name: Optional[str] = None
    cv_match_score: Optional[float] = None
    pre_screen_score_100: Optional[float] = None
    requirements_fit_score_100: Optional[float] = None
    taali_score_100: Optional[float] = None
    # Live-derived label (see pre_screen_snapshot), not a stored value.
    recommendation: Optional[str] = None
    created_at: Optional[datetime] = None


class PublicAssessment(BaseModel):
    id: int
    status: Optional[str] = None
    role_id: Optional[int] = None
    task_id: Optional[int] = None
    candidate_id: Optional[int] = None
    application_id: Optional[int] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    scored_at: Optional[datetime] = None
    taali_score: Optional[float] = None
    final_score: Optional[float] = None
    assessment_score: Optional[float] = None


class CreatePublicShareLink(BaseModel):
    mode: str = Field(
        default="client",
        description="'client' (scrubbed: score + summary) or 'recruiter' (full report).",
    )
    expiry: str = Field(default="7d", description="'24h' | '7d' | '30d'.")


class PublicShareLink(BaseModel):
    id: int
    application_id: int
    token: str
    url: str
    mode: str
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
