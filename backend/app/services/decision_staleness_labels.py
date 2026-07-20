"""Human-readable labels for decision-staleness reasons."""

from __future__ import annotations


_REASON_LABELS = {
    "criteria_changed": "Role criteria edited",
    "cv_replaced": "Candidate uploaded a new CV",
    "pre_screen_score_shifted": "Pre-screen score changed",
    "assessment_score_shifted": "Assessment score changed",
    "cutoff_changed": "Pre-screen cutoff changed",
    "recruiter_note_added": "Recruiter note added",
    "engine_outdated": "Scored by an older model",
    "score_refresh_required": "Candidate score refresh pending",
    "score_generation_changed": "Candidate was re-scored after this decision",
    "policy_generation_changed": "Role decision policy changed",
    "screening_questions_changed": "Application screening questions changed",
    "screening_questions_unavailable": "Application screening questions could not be verified",
}


def summarize_staleness(reasons: list[str]) -> str:
    """Return the one-line Decision Hub summary for ordered reasons."""
    if not reasons:
        return ""
    primary = _REASON_LABELS.get(reasons[0], reasons[0])
    if len(reasons) == 1:
        return primary
    return f"{primary} (+{len(reasons) - 1} more)"


__all__ = ["summarize_staleness"]
