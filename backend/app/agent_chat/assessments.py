"""Read the per-criterion assessments the scorer already stored.

The full CV match persists a per-requirement breakdown (`status` + `reasoning` +
`priority`) in ``candidate_application.cv_match_details['requirements_assessment']``,
keyed by ``requirement_id = "crit_<criterion_id>"``. That's the data a criteria
change should be *reasoned over* — who currently meets/misses the criterion and
**why** — instead of re-scoring the whole pool. This module surfaces it; nothing
here calls an LLM or mutates anything. See docs/REASONED_CRITERIA_CHANGES.md (P1).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..candidate_search.application_role_scope import application_outcome_expression
from ..candidate_search.role_scope import resolve_candidate_role_scope
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication

# The buckets a stored assessment status falls into. Anything else → unknown.
_STATUSES = ("met", "missing", "unknown")


def _req_id(criterion_id: int) -> str:
    return f"crit_{int(criterion_id)}"


def _entry_for(details: Any, rid: str) -> dict[str, Any] | None:
    if not isinstance(details, dict):
        return None
    for e in details.get("requirements_assessment") or []:
        if isinstance(e, dict) and str(e.get("requirement_id")) == rid:
            return e
    return None


def _role_assessment_rows(
    db: Session,
    role: Any,
    *,
    open_only: bool = False,
) -> list[tuple[Any, str | None]]:
    """Return live role members projected onto role-owned scoring details."""

    scope = resolve_candidate_role_scope(
        db,
        organization_id=int(role.organization_id),
        role_id=int(role.id),
    )
    query = (
        db.query(CandidateApplication, Candidate.full_name)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(CandidateApplication.organization_id == int(role.organization_id))
    )
    query = scope.scope_visible_roster(query)
    if open_only:
        query = query.filter(application_outcome_expression(scope) == "open")
    rows = query.all()
    application_ids = [int(application.id) for application, _ in rows]
    adapter = scope.row_adapter(
        scope.evaluation_map(db, application_ids=application_ids)
    )
    return [
        (adapter(application) if adapter is not None else application, full_name)
        for application, full_name in rows
    ]


def criterion_breakdown(
    db: Session, role: Any, criterion_id: int, *, sample: int = 5
) -> dict[str, Any]:
    """Group the role's scored candidates by their STORED status for one
    criterion (met / missing / unknown / not_assessed), with a reasoning sample
    per bucket. Read-only — the basis for scoping a change to this criterion."""
    rid = _req_id(criterion_id)
    rows = _role_assessment_rows(db, role)

    groups: dict[str, list[dict[str, Any]]] = {
        "met": [], "missing": [], "unknown": [], "not_assessed": []
    }
    for app, full_name in rows:
        entry = _entry_for(app.cv_match_details, rid)
        if entry is None:
            bucket = "not_assessed"
            reasoning = None
        else:
            status = str(entry.get("status") or "unknown").lower()
            bucket = status if status in _STATUSES else "unknown"
            reasoning = entry.get("reasoning")
        groups[bucket].append({
            "application_id": int(app.id),
            "candidate_name": full_name or f"#{app.id}",
            "reasoning": reasoning,
        })

    return {
        "criterion_id": int(criterion_id),
        "total": len(rows),
        "counts": {k: len(v) for k, v in groups.items()},
        # Trim reasoning in the sample so the tool result stays compact.
        "samples": {
            k: [
                {**c, "reasoning": (c["reasoning"] or "")[:300] or None}
                for c in v[:sample]
            ]
            for k, v in groups.items()
        },
    }


def affected_applications(
    db: Session, role: Any, criterion_id: int, *, statuses: tuple[str, ...]
) -> list[dict[str, Any]]:
    """The candidates whose stored status for the criterion is in ``statuses`` —
    the scoped set to re-decide (e.g. a widening re-checks only ``missing``).
    Carries each one's stored reasoning so the re-decide can reason over it
    without re-reading the CV."""
    rid = _req_id(criterion_id)
    want = {s.lower() for s in statuses}
    rows = _role_assessment_rows(db, role)
    out: list[dict[str, Any]] = []
    for app, full_name in rows:
        entry = _entry_for(app.cv_match_details, rid)
        if entry is None:
            continue
        status = str(entry.get("status") or "unknown").lower()
        if status in want:
            out.append({
                "application_id": int(app.id),
                "candidate_name": full_name or f"#{app.id}",
                "status": status,
                "reasoning": entry.get("reasoning"),
                "evidence_quotes": entry.get("evidence_quotes") or [],
            })
    return out


__all__ = ["criterion_breakdown", "affected_applications"]
