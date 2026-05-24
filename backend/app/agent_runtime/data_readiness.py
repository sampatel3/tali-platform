"""Data-readiness guardrails for the autonomous agent.

The agent must never spend Claude tokens evaluating a role it has no real
basis to judge. Two preconditions, checked at the top of every cycle:

1. **Job spec present.** An agent-on role with no job description (and no
   free-text description fallback) can't have a meaningful hiring bar — any
   verdict would be a guess. The cycle aborts *before* the first Anthropic
   call ($0) and a ``missing_job_spec`` HITL item is raised so the recruiter
   knows to add one. The next cycle that finds a spec auto-resolves it.

2. **CVs present.** Candidates with no CV can't be scored. The cohort tools
   already skip them, but they'd otherwise vanish silently — so we surface a
   single role-level ``missing_cv`` item with the live count, and clear it
   when every candidate has a CV.

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


def missing_cv_count(db: Session, *, role: Role) -> int:
    """Count of open (non-resolved) applications on the role with no CV text."""
    return int(
        db.query(func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.role_id == role.id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            or_(
                CandidateApplication.cv_text.is_(None),
                func.trim(CandidateApplication.cv_text) == "",
            ),
        )
        .scalar()
        or 0
    )


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


def sync_missing_cv(db: Session, *, role: Role) -> int:
    """Raise/refresh or clear the role-level missing_cv item based on the live
    count. Returns the current count of CV-less candidates."""
    count = missing_cv_count(db, role=role)
    try:
        if count > 0:
            from ..actions import ask_recruiter
            from ..actions.types import Actor

            noun = "candidate" if count == 1 else "candidates"
            ask_recruiter.open(
                db,
                Actor.system(),
                organization_id=int(role.organization_id),
                role_id=int(role.id),
                kind="missing_cv",
                prompt=(
                    f"{count} {noun} on '{role.name}' {'has' if count == 1 else 'have'} "
                    f"no CV on file, so I can't score or decide on "
                    f"{'them' if count != 1 else 'this one'}. Upload the missing "
                    f"CVs (or let the Workable sync pull them) and I'll pick "
                    f"{'them' if count != 1 else 'it'} up next cycle."
                ),
            )
        else:
            resolve_open(db, role=role, kind="missing_cv")
    except Exception:
        logger.warning("sync_missing_cv failed role_id=%s", role.id, exc_info=True)
    return count
