"""HTTP surface for agent_needs_input rows.

Recruiters answer the agent's open questions inline on the role page.
Each route is org-scoped; only the current user's org rows are visible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, joinedload

from . import data_readiness
from ..actions import ask_recruiter as ask_recruiter_action
from ..actions.types import Actor
from ..deps import get_current_user
from ..models.agent_needs_input import AgentNeedsInput
from ..models.organization import Organization
from ..models.user import User
from ..platform.database import get_db

# Defensive ceiling on a single bulk reject. Real no-CV cohorts after a
# Workable sync are tiny (a handful), but a large back-fill could in theory
# leave hundreds file-less; cap synchronous rejects so one request can't make
# hundreds of Workable round-trips. Above this the recruiter reviews + rejects
# in smaller batches from the role page.
MAX_BULK_REJECT_MISSING_CV = 200


router = APIRouter(prefix="/agent-needs-input", tags=["agent-needs-input"])


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class NeedsInputView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    role_id: int
    role_name: str | None = None
    kind: str
    prompt: str
    options: list[dict[str, Any]] | None = None
    response_schema: dict[str, Any] | None = None
    rationale: str | None = None
    status: str  # 'open' | 'resolved' | 'dismissed'
    response: dict[str, Any] | None = None
    resolved_at: datetime | None = None
    resolved_by_user_id: int | None = None
    created_at: datetime
    # Settings-tab deep-link the recruiter can click instead of typing
    # a free-text answer (populated for intent_slot_missing /
    # task_assignment_missing). Stored on response_schema under
    # link_url / link_label keys; pulled into top-level fields here so
    # the frontend doesn't have to dig into response_schema.
    link_url: str | None = None
    link_label: str | None = None

    @classmethod
    def from_row(cls, row: AgentNeedsInput) -> "NeedsInputView":
        if row.resolved_at is not None:
            status = "resolved"
        elif row.dismissed_at is not None:
            status = "dismissed"
        else:
            status = "open"
        schema = row.response_schema if isinstance(row.response_schema, dict) else {}
        return cls(
            id=int(row.id),
            role_id=int(row.role_id),
            role_name=row.role.name if row.role is not None else None,
            kind=row.kind,
            prompt=row.prompt,
            options=row.options,
            response_schema=row.response_schema,
            rationale=row.rationale,
            status=status,
            response=row.response,
            resolved_at=row.resolved_at,
            resolved_by_user_id=(
                int(row.resolved_by_user_id)
                if row.resolved_by_user_id is not None
                else None
            ),
            created_at=row.created_at,
            link_url=schema.get("link_url"),
            link_label=schema.get("link_label"),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[NeedsInputView])
def list_needs_input(
    role_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NeedsInputView]:
    """List open (default) / resolved / all needs-input rows for the
    current org. Optionally filter by ``role_id``.
    """
    # joinedload role so NeedsInputView.from_row's row.role.name access
    # doesn't trigger N+1 queries on the list endpoint (Codex #185).
    q = db.query(AgentNeedsInput).options(
        joinedload(AgentNeedsInput.role),
    ).filter(
        AgentNeedsInput.organization_id == user.organization_id
    )
    if role_id is not None:
        q = q.filter(AgentNeedsInput.role_id == int(role_id))
    if status == "open":
        q = q.filter(
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
    elif status == "resolved":
        q = q.filter(AgentNeedsInput.resolved_at.isnot(None))
    elif status == "dismissed":
        q = q.filter(AgentNeedsInput.dismissed_at.isnot(None))
    elif status not in (None, "all"):
        raise HTTPException(
            status_code=422,
            detail="status must be one of open / resolved / dismissed / all",
        )
    rows = (
        q.order_by(AgentNeedsInput.created_at.desc()).limit(limit).all()
    )
    return [NeedsInputView.from_row(r) for r in rows]


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    response: dict[str, Any]


@router.post("/{needs_input_id}/answer", response_model=NeedsInputView)
def answer_needs_input(
    needs_input_id: int,
    body: AnswerBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NeedsInputView:
    actor = Actor.recruiter(user)
    row = ask_recruiter_action.answer(
        db,
        actor,
        organization_id=int(user.organization_id),
        needs_input_id=needs_input_id,
        response=body.response,
    )
    db.commit()
    db.refresh(row)
    return NeedsInputView.from_row(row)


@router.post("/{needs_input_id}/dismiss", response_model=NeedsInputView)
def dismiss_needs_input(
    needs_input_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NeedsInputView:
    actor = Actor.recruiter(user)
    row = ask_recruiter_action.dismiss(
        db,
        actor,
        organization_id=int(user.organization_id),
        needs_input_id=needs_input_id,
    )
    db.commit()
    db.refresh(row)
    return NeedsInputView.from_row(row)


class RejectMissingCvResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rejected: int
    # Candidates whose Workable disqualification failed — left open, not
    # rejected. Shape: {application_id, reason}.
    failed: list[dict[str, Any]]
    # Live count of file-less candidates still open after the action (0 when
    # everything was rejected; the card resolves itself in that case).
    remaining: int


@router.post(
    "/{needs_input_id}/reject-missing-cv",
    response_model=RejectMissingCvResult,
)
def reject_missing_cv(
    needs_input_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RejectMissingCvResult:
    """Bulk-reject every candidate on the role that has no CV file at all.

    Scoped strictly to the ``missing_cv`` cohort (no CV text *and* no CV
    file). ``cv_unreadable`` candidates — who submitted a CV we simply
    couldn't parse — are never touched here; rejecting them for "no CV" would
    be wrong, so they stay on their own card.

    Each reject writes the Workable disqualification first and flips the local
    outcome only on success, committing per-candidate so a mid-batch failure
    never leaves Workable and our state diverged. Candidates whose Workable
    write fails are reported back as ``failed`` and left open.
    """
    row = (
        db.query(AgentNeedsInput)
        .options(joinedload(AgentNeedsInput.role))
        .filter(
            AgentNeedsInput.id == needs_input_id,
            AgentNeedsInput.organization_id == user.organization_id,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    if row.kind != "missing_cv":
        raise HTTPException(
            status_code=422,
            detail="reject-missing-cv only applies to missing_cv items",
        )
    if not row.is_open:
        raise HTTPException(
            status_code=409, detail="this item is already resolved or dismissed"
        )
    role = row.role
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")

    # Pull one over the cap so we can tell "exactly at cap" from "too many".
    apps = data_readiness.file_less_open_applications(
        db, role=role, limit=MAX_BULK_REJECT_MISSING_CV + 1
    )
    if len(apps) > MAX_BULK_REJECT_MISSING_CV:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{MAX_BULK_REJECT_MISSING_CV}+ candidates have no CV — too many to "
                f"reject in one action. Review them on the role page and reject "
                f"in smaller batches."
            ),
        )

    from ..services.application_automation_service import reject_for_missing_cv

    org = (
        db.query(Organization)
        .filter(Organization.id == user.organization_id)
        .one_or_none()
    )

    rejected = 0
    failed: list[dict[str, Any]] = []
    for app in apps:
        try:
            result = reject_for_missing_cv(
                db=db,
                org=org,
                app=app,
                role=role,
                actor_type="recruiter",
                actor_id=user.id,
            )
            # Commit per-candidate: a successful reject (or a persisted
            # write-back-failure event) stands on its own, so one later
            # failure can't roll back candidates already rejected.
            db.commit()
        except Exception:  # never let one bad row abort the whole batch
            db.rollback()
            failed.append(
                {"application_id": int(app.id), "reason": "unexpected error during reject"}
            )
            continue
        if result.get("performed"):
            rejected += 1
        else:
            failed.append(
                {
                    "application_id": int(app.id),
                    "reason": result.get("reason") or "reject failed",
                }
            )

    # Refresh/resolve the card (and the cv_unreadable card) against the new
    # live counts: when nothing file-less remains, missing_cv auto-resolves
    # and the card disappears on the next reload.
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()

    remaining = data_readiness.missing_cv_count(db, role=role)
    return RejectMissingCvResult(rejected=rejected, failed=failed, remaining=remaining)
