"""Aggregate cv_match_v4 evidence into a candidate-specific interview kit.

The kit is a deterministic projection of the per-criterion data Claude
already returned during scoring (criterion_id, status, evidence_type,
blocker, interview_probe, etc.). No new Claude call — that's the point: the
v4 schema already captures the per-criterion probe text, so the report
layer just needs to filter, group, and rank it sensibly.

Returns ``None`` when the application has no v4 score yet, so callers can
omit the section from the UI without rendering an empty placeholder.
"""

from __future__ import annotations

from typing import Any

from ..models.candidate_application import CandidateApplication


_PRIORITY_STATUSES = {"missing", "partially_met", "unknown"}
_AT_RISK_EVIDENCE_TYPES = {"absent", "implied", "contradicted"}


def _candidate_assessment_entries(details: dict) -> list[dict]:
    raw = details.get("requirements_assessment")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _is_priority(entry: dict) -> bool:
    """A criterion needs interviewer probing when it's a must-have gap or when
    the model's confidence is low / quote was unverifiable."""
    if entry.get("blocker"):
        return True
    status = str(entry.get("status") or "").lower()
    if status in _PRIORITY_STATUSES:
        return True
    evidence_type = str(entry.get("evidence_type") or "").lower()
    if evidence_type in _AT_RISK_EVIDENCE_TYPES:
        return True
    try:
        confidence = float(entry.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return confidence < 0.6


def _rank_key(entry: dict) -> tuple[int, int, float]:
    # Sort blockers first, then must-haves, then by ascending confidence.
    blocker_rank = 0 if entry.get("blocker") else 1
    must_have_rank = 0 if entry.get("must_have") else 1
    try:
        confidence = float(entry.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return (blocker_rank, must_have_rank, confidence)


def _kit_item(entry: dict) -> dict[str, Any]:
    return {
        "criterion_id": entry.get("criterion_id"),
        "criterion_text": entry.get("criterion_text"),
        "must_have": bool(entry.get("must_have")),
        "blocker": bool(entry.get("blocker")),
        "status": entry.get("status"),
        "confidence": entry.get("confidence"),
        "evidence_type": entry.get("evidence_type"),
        "cv_quote": entry.get("cv_quote"),
        "risk_level": entry.get("risk_level"),
        "screening_recommendation": entry.get("screening_recommendation"),
        "interview_probe": entry.get("interview_probe"),
    }


def build_candidate_interview_kit_from_details(details: dict | None) -> dict[str, Any] | None:
    """Build the kit directly from a ``cv_match_details`` blob.

    Useful for tests and for code paths that already have the dict in hand.
    Returns ``None`` if the row is not v4-shaped or has no criteria.
    """
    if not isinstance(details, dict):
        return None
    if str(details.get("scoring_version") or "") != "cv_match_v4":
        return None
    entries = _candidate_assessment_entries(details)
    if not entries:
        return None

    blockers = sorted([e for e in entries if e.get("blocker")], key=_rank_key)
    priority_probes = sorted(
        [e for e in entries if _is_priority(e) and not e.get("blocker")],
        key=_rank_key,
    )
    confirmed = sorted(
        [
            e for e in entries
            if str(e.get("status") or "").lower() == "met"
            and not _is_priority(e)
            and not e.get("blocker")
        ],
        key=_rank_key,
    )

    return {
        "scoring_version": "cv_match_v4",
        "knockout_checks": [_kit_item(e) for e in blockers],
        "priority_probes": [_kit_item(e) for e in priority_probes],
        "confirmed_strengths": [_kit_item(e) for e in confirmed],
        "summary": {
            "total_criteria": len(entries),
            "blockers": len(blockers),
            "needs_probing": len(priority_probes),
            "confirmed": len(confirmed),
        },
    }


def build_candidate_interview_kit_for_application(
    application: CandidateApplication,
) -> dict[str, Any] | None:
    details = getattr(application, "cv_match_details", None)
    return build_candidate_interview_kit_from_details(details)
