"""Canonical recruiter-facing decision reasoning.

Every Decision Hub card should read the same way regardless of which producer
queued it — the LLM agent (judgment cases) or the deterministic bulk pass
(scale). Both funnel through ``queue_decision``; this is the single helper that
derives a recruiter-facing summary from the candidate's stored cv_match
analysis, so a card is never blank or a generic placeholder just because the
producer didn't write a per-candidate rationale.

Sourced from the cv_match ``summary`` (the same field that drives the candidate
report's recommendation hero text and the Workable note), falling back to the
first score-rationale bullet. Returns ``None`` when no qualitative narrative
exists; the caller then substitutes the audit-oriented policy basis.
"""
from __future__ import annotations

from typing import Any

from .decision_presentation_service import normalize_candidate_summary


def recruiter_decision_reasoning(app: Any) -> str | None:
    """Return the recruiter-facing narrative for ``app`` from its cv_match
    analysis, or ``None`` when none is stored."""
    details = getattr(app, "cv_match_details", None)
    if not isinstance(details, dict):
        return None
    summary = normalize_candidate_summary(details.get("summary"))
    if summary:
        return summary
    bullets = details.get("score_rationale_bullets")
    if isinstance(bullets, list):
        for bullet in bullets:
            text = normalize_candidate_summary(bullet)
            if text:
                return text
    return None
