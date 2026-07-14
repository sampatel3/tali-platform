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
    requirement_id = str(row.get("requirement_id") or row.get("criterion_id") or "").strip()
    # ``holistic_*`` priorities are inferred by the scorer from JD wording;
    # they are not recruiter-configured role criteria.  Treating an inferred
    # ``is_core`` label as a hard rail is what turned this candidate's preferred
    # knowledge-graph criteria into an automatic reject.  An explicit blocker
    # remains authoritative; otherwise only canonical/non-holistic assessment
    # priorities can activate the must-have policy.
    inferred_holistic_priority = requirement_id.startswith("holistic_") and not blocker
    return (
        status in _BLOCKED_STATUSES
        and not inferred_holistic_priority
        and (blocker or priority in _MUST_PRIORITIES)
    )


def blocked_must_have_requirements(app: CandidateApplication) -> list[dict[str, Any]]:
    """Return the structured requirement rows that activate the hard rail.

    The policy engine only needs the boolean exposed by :func:`must_have_blocked`,
    but recruiter surfaces need to explain *which* requirements caused it.  Keep
    both reads on this one predicate so the displayed reason can never drift from
    the rule that actually fired.
    """
    blocked: list[dict[str, Any]] = []
    seen: set[str] = set()
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
        for key in ("requirements_assessment", "criteria_assessment", "requirement_results"):
            rows = blob.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not _assessment_blocks(row):
                    continue
                label = str(
                    row.get("criterion_text")
                    or row.get("requirement")
                    or row.get("label")
                    or row.get("name")
                    or "Must-have requirement"
                ).strip()
                status = str(row.get("status") or row.get("result") or "missing").strip().lower()
                dedup_key = f"{label.lower()}::{status}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                blocked.append(
                    {
                        "label": label,
                        "status": status,
                        "priority": str(
                            row.get("priority") or row.get("bucket") or "must_have"
                        ).strip().lower(),
                    }
                )
    return blocked


def must_have_blocked(app: CandidateApplication) -> bool:
    """Read an explicit must-have failure from current persisted evidence.

    This intentionally does not keyword-match CV text or recruiter prose.  A
    hard rejection flag is emitted only when a scoring stage wrote a structured
    blocker, keeping the deterministic rail auditable and fail-safe.
    """
    if blocked_must_have_requirements(app):
        return True

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
    return False


__all__ = ["blocked_must_have_requirements", "must_have_blocked"]
