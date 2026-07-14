"""Canonical, provider-neutral ATS context for application-facing agents.

Agents must not infer Bullhorn state from Workable-only fields.  This module
reduces the integration state to one small contract that can be embedded in
MCP, autonomous-agent, and recruiter-chat payloads without exposing provider
credentials or large raw integration blobs.
"""

from __future__ import annotations

from typing import Any

from ..domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
    normalize_pipeline_key,
)
from ..models.candidate_application import CandidateApplication


def provider_for_application(app: CandidateApplication) -> str:
    """Return the ATS that owns this application, falling back to native."""
    if getattr(app, "bullhorn_job_submission_id", None) or getattr(app, "bullhorn_status", None):
        return "bullhorn"
    if getattr(app, "workable_candidate_id", None) or getattr(app, "workable_stage", None):
        return "workable"
    return "native"


def is_post_handover_application(app: CandidateApplication) -> bool:
    """True when any trustworthy local/external signal says a human advanced it."""
    if normalize_pipeline_key(getattr(app, "pipeline_stage", None)) == "advanced":
        return True
    if is_post_handover_workable_stage(getattr(app, "workable_stage", None)):
        return True
    normalized = normalize_pipeline_key(getattr(app, "external_stage_normalized", None))
    return normalized == "advanced"


def application_ats_context(app: CandidateApplication) -> dict[str, Any]:
    """Return the compact, normalized ATS state agents may reason against."""
    provider = provider_for_application(app)
    if provider == "bullhorn":
        raw_stage = getattr(app, "bullhorn_status", None) or getattr(app, "external_stage_raw", None)
    elif provider == "workable":
        raw_stage = getattr(app, "workable_stage", None) or getattr(app, "external_stage_raw", None)
    else:
        raw_stage = None

    normalized = normalize_pipeline_key(getattr(app, "external_stage_normalized", None)) or None
    needs_mapping = bool(provider == "bullhorn" and raw_stage and not normalized)
    return {
        "provider": provider,
        "raw_stage": raw_stage,
        "normalized_stage": normalized,
        "needs_mapping": needs_mapping,
        "post_handover": is_post_handover_application(app),
        "writeback_linked": bool(
            getattr(app, "bullhorn_job_submission_id", None)
            if provider == "bullhorn"
            else getattr(app, "workable_candidate_id", None)
            if provider == "workable"
            else False
        ),
    }


__all__ = [
    "application_ats_context",
    "is_post_handover_application",
    "provider_for_application",
]
