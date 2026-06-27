"""Read the per-criterion assessments the scorer already stored.

The full CV match persists a per-requirement breakdown (`status` + `reasoning` +
`priority`) in ``candidate_application.cv_match_details['requirements_assessment']``.
Two schemas are live in prod: the v3 rows key on ``requirement_id =
"crit_<criterion_id>"`` (a string) and carry ``reasoning`` + ``evidence_quotes``;
the newer cv_match_v4 rows key on an integer ``criterion_id`` and carry a single
verified ``cv_quote`` + an ``interview_probe``. ``_entry_for`` matches either, so
a v4-scored role isn't reported as entirely "not_assessed". That's the data a
criteria change should be *reasoned over* — who currently meets/misses the
criterion and **why** — instead of re-scoring the whole pool. This module
surfaces it; nothing here calls an LLM or mutates anything. See
docs/REASONED_CRITERIA_CHANGES.md (P1).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication

# The buckets a stored assessment status falls into. Anything else → unknown.
_STATUSES = ("met", "missing", "unknown")


def _req_id(criterion_id: int) -> str:
    return f"crit_{int(criterion_id)}"


def _criterion_id_from_rid(rid: str) -> int | None:
    """Recover the integer criterion id from a v3 ``requirement_id`` ("crit_<n>")."""
    text = str(rid or "")
    if text.startswith("crit_"):
        text = text[len("crit_"):]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _entry_for(details: Any, rid: str) -> dict[str, Any] | None:
    """Find the stored assessment row for a criterion across BOTH cv_match schemas.

    v3 keys each row by ``requirement_id`` (the string ``rid`` = "crit_<n>");
    cv_match_v4 keys them by an integer ``criterion_id``. Match either — without
    the v4 branch a v4-scored role's rows never match and every candidate falls
    into ``not_assessed``. ``rid`` stays the public parameter so every caller
    (the health checks + the breakdown/affected helpers) is unchanged.
    """
    if not isinstance(details, dict):
        return None
    numeric_id = _criterion_id_from_rid(rid)
    for e in details.get("requirements_assessment") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("requirement_id")) == rid:
            return e
        if numeric_id is not None and e.get("criterion_id") is not None:
            try:
                if int(e["criterion_id"]) == numeric_id:
                    return e
            except (TypeError, ValueError):
                pass
    return None


def _entry_reasoning(entry: dict[str, Any]) -> str | None:
    """The "why" for the criteria-change reasoning sample. v3 stores free-text
    ``reasoning``; v4 has no reasoning field, so fall back to the
    ``interview_probe`` (what to ask to close/confirm the gap)."""
    return entry.get("reasoning") or entry.get("interview_probe")


def _entry_evidence_quotes(entry: dict[str, Any]) -> list[str]:
    """v3 stores a list under ``evidence_quotes``; v4 stores a single verified
    ``cv_quote`` (string|null). Return a list of quotes either way."""
    quotes = entry.get("evidence_quotes")
    if isinstance(quotes, list) and quotes:
        return quotes
    quote = entry.get("cv_quote")
    return [quote] if quote else []


def criterion_breakdown(
    db: Session, role: Any, criterion_id: int, *, sample: int = 5
) -> dict[str, Any]:
    """Group the role's scored candidates by their STORED status for one
    criterion (met / missing / unknown / not_assessed), with a reasoning sample
    per bucket. Read-only — the basis for scoping a change to this criterion."""
    rid = _req_id(criterion_id)
    rows = (
        db.query(CandidateApplication, Candidate.full_name)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.deleted_at.is_(None),
        )
        .all()
    )

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
            reasoning = _entry_reasoning(entry)
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
    rows = (
        db.query(CandidateApplication, Candidate.full_name)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.cv_match_details.isnot(None),
        )
        .all()
    )
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
                "reasoning": _entry_reasoning(entry),
                "evidence_quotes": _entry_evidence_quotes(entry),
            })
    return out


__all__ = ["criterion_breakdown", "affected_applications"]
