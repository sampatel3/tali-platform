"""Data-readiness guardrails for the autonomous agent.

The agent must never spend Claude tokens evaluating a role it has no real
basis to judge. Two preconditions, checked at the top of every cycle:

1. **Job spec present.** An agent-on role with no job description (and no
   free-text description fallback) can't have a meaningful hiring bar — any
   verdict would be a guess. The cycle aborts *before* the first Anthropic
   call ($0) and a ``missing_job_spec`` HITL item is raised so the recruiter
   knows to add one. The next cycle that finds a spec auto-resolves it.

2. **CVs present *and readable*.** Candidates with no CV text can't be
   scored. The cohort tools already skip them, but they'd otherwise vanish
   silently — so we surface them, split by cause so the recruiter gets an
   honest remedy:

   - ``missing_cv`` — no CV file at all on record (Workable had nothing to
     pull, or the candidate never attached one). Surfaced with the live
     count and a one-click "Reject — no CV" action: there's nothing to
     fetch, so the recruiter's only real choices are upload one or reject.
   - ``cv_unreadable`` — a CV *file* is on record but no text could be
     extracted (a scanned image / photo with no text layer). Re-fetching
     won't help (the sync already holds the file) and re-uploading the same
     scan won't either — it needs OCR or a text-based file. Distinct item,
     and deliberately NOT eligible for the no-CV reject (the candidate did
     submit a CV; rejecting them for "no CV" would be wrong).

   Both clear automatically once the gap is filled.

All helpers are best-effort and idempotent (the underlying ask_recruiter
upsert keys on ``(role_id, kind, subject_id)``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models.agent_needs_input import AgentNeedsInput
from ..models.candidate_application import CandidateApplication
from ..models.role import Role

logger = logging.getLogger("taali.agent_runtime.data_readiness")


def has_job_spec(role: Role) -> bool:
    """True when the role has any usable hiring context (spec or description)."""
    return bool((role.job_spec_text or "").strip() or (role.description or "").strip())


def _open_no_cv_text_query(db: Session, *, role: Role):
    """Base query: open, non-deleted applications on the role that have no
    extracted CV text — the agent can't score any of these. Callers narrow
    further by whether a CV *file* exists (missing vs. unreadable)."""
    return db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == role.organization_id,
        CandidateApplication.role_id == role.id,
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.application_outcome == "open",
        or_(
            CandidateApplication.cv_text.is_(None),
            func.trim(CandidateApplication.cv_text) == "",
        ),
    )


def _no_cv_file_clause():
    """SQL predicate: the application has no CV file on record."""
    return or_(
        CandidateApplication.cv_file_url.is_(None),
        func.trim(CandidateApplication.cv_file_url) == "",
    )


def missing_cv_count(db: Session, *, role: Role) -> int:
    """Count of open applications with neither CV text nor a CV file on
    record — nothing was ever fetched or uploaded. These are the candidates
    the "Reject — no CV" action targets."""
    return int(
        _open_no_cv_text_query(db, role=role)
        .filter(_no_cv_file_clause())
        .with_entities(func.count(CandidateApplication.id))
        .scalar()
        or 0
    )


def unreadable_cv_count(db: Session, *, role: Role) -> int:
    """Count of open applications that have a CV *file* on record but no
    extracted text — a scanned image / photo CV the parser couldn't read.
    Re-fetching won't help (the sync already holds the file), so these are
    surfaced separately from ``missing_cv`` and are NOT eligible for the
    no-CV reject shortcut."""
    return int(
        _open_no_cv_text_query(db, role=role)
        .filter(
            CandidateApplication.cv_file_url.isnot(None),
            func.trim(CandidateApplication.cv_file_url) != "",
        )
        .with_entities(func.count(CandidateApplication.id))
        .scalar()
        or 0
    )


def file_less_open_applications(
    db: Session, *, role: Role, limit: int | None = None
) -> list[CandidateApplication]:
    """The rejectable cohort: open applications with neither CV text nor a
    CV file. Returns the rows themselves so the recruiter's "Reject — no CV"
    action can act on each. Mirrors ``missing_cv_count``'s predicate exactly
    so the count shown and the rows rejected never disagree."""
    q = _open_no_cv_text_query(db, role=role).filter(_no_cv_file_clause())
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def raise_missing_job_spec(db: Session, *, role: Role) -> None:
    """Open (or refresh) the missing_job_spec HITL item. Best-effort."""
    try:
        from ..actions import ask_recruiter
        from ..actions.types import Actor

        ask_recruiter.open(
            db,
            Actor.system(),
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            kind="missing_job_spec",
            # Replaced by the canonical plain-English prompt in ask_recruiter.
            prompt="Role has no job description.",
        )
    except Exception:
        logger.warning("raise_missing_job_spec failed role_id=%s", role.id, exc_info=True)


def resolve_open(db: Session, *, role: Role, kind: str) -> int:
    """System auto-resolve: close any open rows of ``kind`` for the role once
    the underlying gap is filled. Returns how many rows were closed."""
    rows = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == role.organization_id,
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == kind,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.resolved_at = now
        row.response = {"value": "auto_resolved", "auto_resolved": True}
        db.add(row)
    return len(rows)


def sync_cv_readiness(db: Session, *, role: Role) -> dict[str, int]:
    """Raise/refresh or clear the role-level ``missing_cv`` and
    ``cv_unreadable`` items based on their live counts. Returns both counts
    keyed by kind. Best-effort: a failure here never blocks the cycle."""
    missing = missing_cv_count(db, role=role)
    unreadable = unreadable_cv_count(db, role=role)
    try:
        from ..actions import ask_recruiter
        from ..actions.types import Actor

        if missing > 0:
            noun = "candidate" if missing == 1 else "candidates"
            ask_recruiter.open(
                db,
                Actor.system(),
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                kind="missing_cv",
                prompt=(
                    f"{missing} {noun} on '{role.name}' {'has' if missing == 1 else 'have'} "
                    f"no CV on file, so I can't score or decide on "
                    f"{'them' if missing != 1 else 'this one'}. Upload the missing "
                    f"CVs (or let the Workable sync pull them) and I'll pick "
                    f"{'them' if missing != 1 else 'it'} up next cycle."
                ),
            )
        else:
            resolve_open(db, role=role, kind="missing_cv")

        if unreadable > 0:
            noun = "candidate" if unreadable == 1 else "candidates"
            ask_recruiter.open(
                db,
                Actor.system(),
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                kind="cv_unreadable",
                prompt=(
                    f"{unreadable} {noun} on '{role.name}' {'has' if unreadable == 1 else 'have'} "
                    f"a CV on file that I couldn't read — most likely a scanned image "
                    f"or photo with no selectable text. I can't score "
                    f"{'them' if unreadable != 1 else 'this one'} until the text is "
                    f"available: re-upload a text-based CV (PDF or DOCX) or run it "
                    f"through OCR, and I'll pick "
                    f"{'them' if unreadable != 1 else 'it'} up next cycle."
                ),
            )
        else:
            resolve_open(db, role=role, kind="cv_unreadable")
    except Exception:
        logger.warning("sync_cv_readiness failed role_id=%s", role.id, exc_info=True)
    return {"missing_cv": missing, "cv_unreadable": unreadable}
