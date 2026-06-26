"""Thin payload builders for MCP responses.

The recruiter API's full ``application_detail_payload`` carries 60+ fields
including interview packs, screening packs, score telemetry, and integration
state — useful for the React UI but noisy for an LLM agent. These builders
return the minimal shape needed to answer "score above X", "advance which
candidate", and "show me this person".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..services.role_criteria_service import render_role_intent_block
from .urls import application_url, candidate_url, role_url

# Score keys exposed via ``search_applications.score_type`` and
# ``compare_applications``.  ``taali`` is the merged primary score; the
# others are component/diagnostic scores.
SCORE_FIELDS: dict[str, str] = {
    "taali": "taali_score_cache_100",
    "pre_screen": "pre_screen_score_100",
    "rank": "rank_score",
    "cv_match": "cv_match_score",
}


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _candidate_location(candidate: Candidate | None) -> str | None:
    if candidate is None:
        return None
    city = (candidate.location_city or "").strip()
    country = (candidate.location_country or "").strip()
    if city and country:
        return f"{city}, {country}"
    return city or country or None


def role_summary(
    role: Role,
    *,
    applications_count: int | None = None,
    stage_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compact role row for ``list_roles`` and embedded references."""
    out: dict[str, Any] = {
        "role_id": role.id,
        "name": role.name,
        "source": role.source,
        "auto_reject": bool(getattr(role, "auto_reject", False)),
        "score_threshold": role.score_threshold,
        "created_at": _isoformat(role.created_at),
        "frontend_url": role_url(role.id),
    }
    if applications_count is not None:
        out["applications_count"] = int(applications_count)
    if stage_counts is not None:
        out["stage_counts"] = dict(stage_counts)
    return out


def role_detail(
    role: Role,
    *,
    applications_count: int | None = None,
    stage_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Full role payload — adds spec text and criteria to the summary."""
    payload = role_summary(
        role,
        applications_count=applications_count,
        stage_counts=stage_counts,
    )
    payload["description"] = role.description
    payload["job_spec_text"] = role.job_spec_text
    payload["recruiter_criteria_text"] = render_role_intent_block(role) or None
    payload["criteria"] = [
        {
            "id": c.id,
            "label": getattr(c, "label", None),
            "weight": getattr(c, "weight", None),
            "ordering": getattr(c, "ordering", None),
        }
        for c in (role.criteria or [])
        if getattr(c, "deleted_at", None) is None
    ]
    return payload


def application_summary(
    app: CandidateApplication,
) -> dict[str, Any]:
    """Compact application row for ``search_applications``.

    Includes all four scores so the agent can sort/compare without a second
    round-trip, plus enough candidate identity to render a useful answer.
    """
    candidate = app.candidate
    role = app.role
    return {
        "application_id": app.id,
        "candidate_id": app.candidate_id,
        "role_id": app.role_id,
        "role_name": role.name if role else None,
        "candidate_name": candidate.full_name if candidate else None,
        "candidate_email": candidate.email if candidate else None,
        "candidate_position": candidate.position if candidate else None,
        "candidate_location": _candidate_location(candidate),
        "pipeline_stage": app.pipeline_stage,
        "application_outcome": app.application_outcome,
        "pipeline_stage_updated_at": _isoformat(app.pipeline_stage_updated_at),
        "workable_stage": app.workable_stage,
        "external_stage_normalized": app.external_stage_normalized,
        "taali_score": app.taali_score_cache_100,
        "pre_screen_score": app.pre_screen_score_100,
        "rank_score": app.rank_score,
        "cv_match_score": app.cv_match_score,
        "workable_score": app.workable_score,
        "auto_reject_state": app.auto_reject_state,
        "created_at": _isoformat(app.created_at),
        "frontend_url": application_url(app.id, role_id=app.role_id),
        "workable_profile_url": getattr(app, "workable_profile_url", None),
    }


def application_detail(
    app: CandidateApplication,
    *,
    include_cv_text: bool = False,
) -> dict[str, Any]:
    """Detailed single application — adds CV preview, evidence, notes."""
    payload = application_summary(app)
    payload["pre_screen_recommendation"] = app.pre_screen_recommendation
    payload["pre_screen_evidence"] = app.pre_screen_evidence
    payload["assessment_score_cache_100"] = app.assessment_score_cache_100
    payload["role_fit_score_cache_100"] = app.role_fit_score_cache_100
    payload["score_mode_cache"] = app.score_mode_cache
    payload["score_cached_at"] = _isoformat(app.score_cached_at)
    payload["auto_reject_reason"] = app.auto_reject_reason
    payload["notes"] = app.notes
    # Per-candidate recruiter notes flagged for the agent ("already
    # interviewed — not suitable", "lacks the technical depth"). Standing
    # guidance the recruiter wrote about THIS candidate; weigh it like the
    # role-level recruiter feedback that rides in the system prompt.
    from ..services.application_notes import recruiter_notes_for_agent

    payload["recruiter_notes"] = recruiter_notes_for_agent(app)
    payload["cv_filename"] = app.cv_filename or (
        app.candidate.cv_filename if app.candidate else None
    )
    cv_text = (app.cv_text or "")
    if not cv_text and app.candidate:
        cv_text = app.candidate.cv_text or ""
    cv_text = cv_text.strip()
    payload["cv_text"] = cv_text if include_cv_text else None
    payload["cv_text_preview"] = (
        (cv_text[:500] + ("..." if len(cv_text) > 500 else "")) if cv_text else None
    )
    return payload


def candidate_detail(candidate: Candidate) -> dict[str, Any]:
    """Cross-role view of a single candidate."""
    return {
        "candidate_id": candidate.id,
        "full_name": candidate.full_name,
        "email": candidate.email,
        "position": candidate.position,
        "headline": candidate.headline,
        "location": _candidate_location(candidate),
        "summary": candidate.summary,
        "skills": candidate.skills,
        "experience_entries": candidate.experience_entries,
        "education_entries": candidate.education_entries,
        "tags": candidate.tags,
        "cv_filename": candidate.cv_filename,
        "cv_uploaded_at": _isoformat(candidate.cv_uploaded_at),
        "created_at": _isoformat(candidate.created_at),
        "frontend_url": candidate_url(candidate.id),
        "applications": [
            {
                "application_id": a.id,
                "role_id": a.role_id,
                "role_name": a.role.name if a.role else None,
                "pipeline_stage": a.pipeline_stage,
                "application_outcome": a.application_outcome,
                "workable_stage": a.workable_stage,
                "taali_score": a.taali_score_cache_100,
                "pre_screen_score": a.pre_screen_score_100,
                "frontend_url": application_url(a.id, role_id=a.role_id),
            }
            for a in (candidate.applications or [])
            if getattr(a, "deleted_at", None) is None
        ],
    }


def comparison_row(app: CandidateApplication) -> dict[str, Any]:
    """Trimmed application shape used inside ``compare_applications``."""
    candidate = app.candidate
    return {
        "application_id": app.id,
        "candidate_name": candidate.full_name if candidate else None,
        "candidate_email": candidate.email if candidate else None,
        "role_id": app.role_id,
        "role_name": app.role.name if app.role else None,
        "pipeline_stage": app.pipeline_stage,
        "application_outcome": app.application_outcome,
        "workable_stage": app.workable_stage,
        "external_stage_normalized": app.external_stage_normalized,
        "scores": {
            "taali": app.taali_score_cache_100,
            "pre_screen": app.pre_screen_score_100,
            "rank": app.rank_score,
            "cv_match": app.cv_match_score,
            "workable": app.workable_score,
            "assessment": app.assessment_score_cache_100,
            "role_fit": app.role_fit_score_cache_100,
        },
        "pre_screen_recommendation": app.pre_screen_recommendation,
        "frontend_url": application_url(app.id, role_id=app.role_id),
    }
