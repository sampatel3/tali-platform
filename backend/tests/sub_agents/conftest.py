"""Helpers for sub-agent tests."""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def make_full_application(
    db,
    *,
    cv_text: str = "Senior Python engineer with 8 years SaaS experience.",
    jd_text: str = "Looking for a senior Python engineer.",
    cv_match_details: dict | None = None,
    pre_screen_score: float | None = None,
    taali_score: float | None = None,
    assessment_score: float | None = None,
):
    """Create org + role + candidate + application with the given fields populated."""
    org = Organization(name="SubAgent Org", slug=f"sa-org-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        description=jd_text,
        job_spec_text=jd_text,
    )
    db.add(role)
    db.flush()
    candidate = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        cv_text=cv_text,
        cv_match_details=cv_match_details,
        pre_screen_score_100=pre_screen_score,
        genuine_pre_screen_score_100=pre_screen_score,
        taali_score_cache_100=taali_score,
        assessment_score_cache_100=assessment_score,
    )
    db.add(app)
    db.flush()
    return org, role, candidate, app
