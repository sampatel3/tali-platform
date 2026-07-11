"""Voluntary EEO / OFCCP self-identification.

Two operations ONLY, on purpose:
- ``record_response`` — the applicant self-reports (idempotent per application).
- ``aggregate_report`` — counts per category, org- (and optionally role-) scoped.

There is deliberately NO "get one person's EEO data" function. These values are
segregated from the scoring/decision path and must never surface per-candidate
to a recruiter or the agent (see the "agent never acts on protected
characteristics" rule). Mutators flush but do NOT commit.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.eeo_response import EEOResponse

_CATEGORIES = ("gender", "race_ethnicity", "veteran_status", "disability_status")

# k-anonymity: aggregate cells with fewer than this many people are suppressed
# in the admin report so a small count can't re-identify an individual.
SMALL_CELL_THRESHOLD = 5


def _application_in_org(
    db: Session, org_id: int, application_id: int
) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == org_id,
        )
        .first()
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def record_response(
    db: Session,
    org_id: int,
    application_id: int,
    *,
    gender: Optional[str] = None,
    race_ethnicity: Optional[str] = None,
    veteran_status: Optional[str] = None,
    disability_status: Optional[str] = None,
    declined_to_answer: bool = False,
) -> EEOResponse:
    """Record (or overwrite) the voluntary self-ID for ONE application. Idempotent
    per application: a second call updates the same row rather than inserting."""
    _application_in_org(db, org_id, application_id)
    row = (
        db.query(EEOResponse)
        .filter(
            EEOResponse.organization_id == org_id,
            EEOResponse.application_id == application_id,
        )
        .first()
    )
    if row is None:
        row = EEOResponse(organization_id=org_id, application_id=application_id)
        db.add(row)
    row.gender = gender
    row.race_ethnicity = race_ethnicity
    row.veteran_status = veteran_status
    row.disability_status = disability_status
    row.declined_to_answer = bool(declined_to_answer)
    db.flush()
    return row


def aggregate_report(
    db: Session, org_id: int, role_id: Optional[int] = None
) -> Dict[str, Any]:
    """Counts only — value → count per category, plus totals. Never returns a row
    that can be tied back to an individual. Raw counts (no suppression) — the
    admin route applies :func:`suppress_small_cells` before it leaves the org."""
    q = (
        db.query(EEOResponse)
        .join(
            CandidateApplication,
            EEOResponse.application_id == CandidateApplication.id,
        )
        .filter(EEOResponse.organization_id == org_id)
    )
    if role_id is not None:
        q = q.filter(CandidateApplication.role_id == role_id)
    rows = q.all()

    report: Dict[str, Any] = {
        "total": len(rows),
        "declined_count": sum(1 for r in rows if r.declined_to_answer),
    }
    for category in _CATEGORIES:
        counts: Dict[str, int] = {}
        for r in rows:
            value = getattr(r, category)
            if value:
                counts[value] = counts.get(value, 0) + 1
        report[category] = counts
    return report


def suppress_small_cells(
    report: Dict[str, Any], min_count: int = SMALL_CELL_THRESHOLD
) -> Dict[str, Any]:
    """Return a k-anonymized copy of a raw aggregate report: any per-category
    value whose count is below ``min_count`` has its count replaced with the
    string ``"<5"`` so a low cell can't be used to re-identify an individual.
    The org-wide ``total`` / ``declined_count`` totals are not protected cells and
    pass through unchanged."""
    out: Dict[str, Any] = {
        "total": report.get("total", 0),
        "declined_count": report.get("declined_count", 0),
    }
    label = f"<{min_count}"
    for category in _CATEGORIES:
        counts = report.get(category, {}) or {}
        out[category] = {
            value: (count if count >= min_count else label)
            for value, count in counts.items()
        }
    return out
