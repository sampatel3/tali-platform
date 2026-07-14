"""Deterministic decision flags derived only from persisted scoring evidence."""

from __future__ import annotations

from typing import Any

from ..models.candidate_application import CandidateApplication

_BLOCKED_STATUSES = frozenset(
    {"missing", "unknown", "not_met", "not met", "failed", "fail", "no"}
)
_MUST_PRIORITIES = frozenset(
    {"must", "must_have", "must-have", "required", "constraint", "knockout"}
)


def _explicit_block(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() == "true"


def _assessment_blocks(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    status = str(row.get("status") or row.get("result") or "").strip().lower()
    blocker = _explicit_block(row.get("blocker")) or _explicit_block(row.get("must_have"))
    priority = str(row.get("priority") or row.get("bucket") or "").strip().lower()
    return status in _BLOCKED_STATUSES and (blocker or priority in _MUST_PRIORITIES)


def must_have_blocked(app: CandidateApplication) -> bool:
    """Read an explicit must-have failure from current persisted evidence.

    This intentionally does not keyword-match CV text or recruiter prose.  A
    hard rejection flag is emitted only when a scoring stage wrote a structured
    blocker, keeping the deterministic rail auditable and fail-safe.
    """
    blobs = [
        getattr(app, "cv_match_details", None),
        getattr(app, "pre_screen_evidence", None),
    ]
    knockout = getattr(app, "screening_answers", None)
    if isinstance(knockout, dict):
        blobs.append(knockout.get("_knockout"))

    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        if _explicit_block(blob.get("must_have_blocked")) or _explicit_block(blob.get("blocked")):
            return True
        nested = blob.get("match_details")
        if isinstance(nested, dict) and _explicit_block(nested.get("must_have_blocked")):
            return True
        for key in ("requirements_assessment", "criteria_assessment", "requirement_results"):
            rows = blob.get(key)
            if isinstance(rows, list) and any(_assessment_blocks(row) for row in rows):
                return True
    return False


__all__ = ["must_have_blocked"]
