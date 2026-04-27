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
_LOW_CONFIDENCE_LABELS = {"low"}
_V3_PROMPT_VERSIONS = {"cv_match_v3.0"}
_V4_SCORING_VERSIONS = {"cv_match_v4"}


def _candidate_assessment_entries(details: dict) -> list[dict]:
    raw = details.get("requirements_assessment")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _confidence_value(entry: dict) -> float:
    """Normalize confidence into 0-1 float.

    v4 emits a numeric confidence (0..1). v3 emits ``high|medium|low``.
    """
    raw = entry.get("confidence")
    if isinstance(raw, str):
        label = raw.strip().lower()
        if label == "high":
            return 0.9
        if label == "medium":
            return 0.6
        if label == "low":
            return 0.3
        try:
            return float(label)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_must_have(entry: dict) -> bool:
    if bool(entry.get("must_have")):
        return True
    priority = str(entry.get("priority") or "").strip().lower()
    return priority == "must_have"


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
    confidence = _confidence_value(entry)
    return confidence < 0.6


def _rank_key(entry: dict) -> tuple[int, int, float]:
    # Sort blockers first, then must-haves, then by ascending confidence.
    blocker_rank = 0 if entry.get("blocker") else 1
    must_have_rank = 0 if _is_must_have(entry) else 1
    return (blocker_rank, must_have_rank, _confidence_value(entry))


def _kit_item(entry: dict) -> dict[str, Any]:
    # Field names differ between v3 and v4. Normalize to the v4 surface so
    # frontend renders work without branching: criterion_id/text/cv_quote.
    return {
        "criterion_id": entry.get("criterion_id") or entry.get("requirement_id"),
        "criterion_text": entry.get("criterion_text") or entry.get("requirement"),
        "must_have": _is_must_have(entry),
        "blocker": bool(entry.get("blocker")),
        "status": entry.get("status"),
        "confidence": entry.get("confidence"),
        "evidence_type": entry.get("evidence_type"),
        "cv_quote": entry.get("cv_quote") or entry.get("evidence_quote"),
        "risk_level": entry.get("risk_level"),
        "screening_recommendation": entry.get("screening_recommendation"),
        "interview_probe": entry.get("interview_probe") or entry.get("impact"),
    }


def _detect_scoring_version(details: dict) -> str | None:
    raw = str(details.get("scoring_version") or "").strip()
    if raw in _V4_SCORING_VERSIONS:
        return "cv_match_v4"
    if raw in _V3_PROMPT_VERSIONS:
        return "cv_match_v3.0"
    prompt_version = str(details.get("prompt_version") or "").strip()
    if prompt_version in _V3_PROMPT_VERSIONS:
        return "cv_match_v3.0"
    return None


def build_candidate_interview_kit_from_details(details: dict | None) -> dict[str, Any] | None:
    """Build the kit directly from a ``cv_match_details`` blob.

    Useful for tests and for code paths that already have the dict in hand.
    Returns ``None`` when the row has no recognisable scoring version OR no
    requirements_assessment entries.
    """
    if not isinstance(details, dict):
        return None
    scoring_version = _detect_scoring_version(details)
    if scoring_version is None:
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
        "scoring_version": scoring_version,
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
