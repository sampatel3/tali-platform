"""Small presentation helpers for serialized agent decisions."""

from __future__ import annotations

from typing import Any, Optional


def confidence_band(value: Optional[float]) -> Optional[str]:
    """Bucket 0-1 confidence into the UI's purple-only tiers."""

    if value is None:
        return None
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


def first_score(*candidates: Any) -> Optional[float]:
    """Return the first finite numeric candidate without skipping zero."""

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            score = float(candidate)
        except (TypeError, ValueError):
            continue
        if score == score and score not in (float("inf"), float("-inf")):
            return score
    return None


def workable_stage_job_id(role: Any, application: Any) -> Optional[str]:
    """Return the exact Workable job whose linked application can be moved.

    Related roles are score-only views, so their ATS owner supplies the job
    shortcode.  The application link remains the decisive gate: a manual or
    Bullhorn application on a Workable-backed role must not be sent through a
    Workable stage picker merely because the role has a shortcode.
    """

    candidate_id = str(getattr(application, "workable_candidate_id", None) or "").strip()
    if not candidate_id or role is None:
        return None
    operational_role = role
    if str(getattr(role, "role_kind", None) or "") == "sister":
        owner = getattr(role, "ats_owner_role", None)
        if (
            owner is not None
            and getattr(owner, "organization_id", None)
            == getattr(role, "organization_id", None)
            and getattr(owner, "deleted_at", None) is None
        ):
            operational_role = owner
    shortcode = str(getattr(operational_role, "workable_job_id", None) or "").strip()
    return shortcode or None


__all__ = ["confidence_band", "first_score", "workable_stage_job_id"]
