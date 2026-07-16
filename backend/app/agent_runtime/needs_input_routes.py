"""HTTP surface for agent_needs_input rows.

Recruiters answer the agent's open questions inline on the role page.
Each route is org-scoped; only the current user's org rows are visible.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, joinedload

from . import data_readiness
from ..actions import ask_recruiter as ask_recruiter_action
from ..actions.types import Actor
from ..agent_chat.recruiter_inputs import recruiter_input_allows_dismiss
from ..deps import get_current_user
from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..models.agent_needs_input import AgentNeedsInput
from ..models.organization import Organization
from ..models.role import Role
from ..models.user import User
from ..platform.database import get_db

logger = logging.getLogger("taali.agent_runtime.needs_input_routes")

# Defensive ceiling on a single bulk reject. Real CV-gap cohorts after a
# Workable sync are tiny (a handful), but a large back-fill could in theory
# leave hundreds; cap synchronous rejects so one request can't make hundreds
# of Workable round-trips. Above this the recruiter reviews + rejects in
# smaller batches from the role page.
MAX_BULK_REJECT_CV_GAP = 200

# The two CV-gap card kinds the bulk reject serves, each with the cohort
# accessor it targets and the Workable/event reason that keeps the audit
# trail honest about *why* the candidate couldn't be evaluated.
_CV_GAP_REJECT = {
    "missing_cv": {
        "cohort": "file_less_open_applications",
        "count": "missing_cv_count",
        "reason": "No CV on file",
        "too_many": "have no CV",
    },
    "cv_unreadable": {
        "cohort": "unreadable_cv_open_applications",
        "count": "unreadable_cv_count",
        "reason": "CV could not be read",
        "too_many": "have an unreadable CV",
    },
}


router = APIRouter(prefix="/agent-needs-input", tags=["agent-needs-input"])


def _enqueue_active_role_followup(db: Session, *, row: AgentNeedsInput) -> None:
    """Best-effort immediate follow-up after recruiter input is committed.

    The hourly cohort beat remains the reliability backstop.  This only closes
    the conversational loop sooner for an enabled, unpaused role; a broker
    failure must never turn a successfully recorded answer/dismissal into an
    HTTP error.
    """

    role_id = getattr(row, "role_id", None)
    try:
        role_id = int(role_id)
        role = (
            db.query(Role.id)
            .filter(
                Role.id == role_id,
                Role.organization_id == int(row.organization_id),
                Role.deleted_at.is_(None),
                Role.agentic_mode_enabled.is_(True),
                Role.agent_paused_at.is_(None),
            )
            .first()
        )
        if role is None:
            return

        from ..tasks.agent_tasks import agent_daily_review_role

        agent_daily_review_role.delay(role_id)
    except Exception:  # pragma: no cover - beat retries the role later
        logger.exception(
            "failed to enqueue recruiter-input follow-up for role_id=%s",
            role_id,
        )


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class NeedsInputView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    role_id: int
    role_name: str | None = None
    # Revision of the shared job configuration when this card was rendered.
    # A setting answer must echo it so an old card cannot overwrite a newer
    # edit made in another browser session.
    role_version: int
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
            role_version=int(row.role.version or 1),
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
    expected_version: int


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
        expected_version=body.expected_version,
    )
    db.commit()
    _enqueue_active_role_followup(db, row=row)
    db.refresh(row)
    return NeedsInputView.from_row(row)


@router.post("/{needs_input_id}/dismiss", response_model=NeedsInputView)
def dismiss_needs_input(
    needs_input_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NeedsInputView:
    policy_row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == needs_input_id,
            AgentNeedsInput.organization_id == int(user.organization_id),
        )
        .one_or_none()
    )
    if policy_row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    if policy_row.is_open and not recruiter_input_allows_dismiss(policy_row):
        raise HTTPException(
            status_code=403,
            detail="this recruiter question must be answered and cannot be dismissed",
        )
    should_follow_up = policy_row.is_open

    actor = Actor.recruiter(user)
    row = ask_recruiter_action.dismiss(
        db,
        actor,
        organization_id=int(user.organization_id),
        needs_input_id=needs_input_id,
    )
    db.commit()
    if should_follow_up:
        _enqueue_active_role_followup(db, row=row)
    db.refresh(row)
    return NeedsInputView.from_row(row)


class RejectCvGapResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rejected: int
    # Candidates whose Workable disqualification failed — left open, not
    # rejected. Shape: {application_id, reason}.
    failed: list[dict[str, Any]]
    # Live count of this card's cohort still open after the action (0 when
    # everything was rejected; the card resolves itself in that case).
    remaining: int


@router.post(
    "/{needs_input_id}/reject-cv-gap",
    response_model=RejectCvGapResult,
)
def reject_cv_gap(
    needs_input_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RejectCvGapResult:
    """Bulk-reject the cohort behind a CV-gap card the agent can't evaluate.

    Works for both ``missing_cv`` (no CV file at all → reason "No CV on
    file") and ``cv_unreadable`` (a CV file we couldn't parse → reason "CV
    could not be read"). Each card rejects only its own cohort and stamps the
    matching reason, so a candidate who *did* submit a CV is never recorded as
    "no CV". The recruiter is the decision-maker — whether to chase an OCR
    re-upload or just reject is their call.

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

    # Candidate rejection is an agent-controlled action on this specific job.
    # The shared Role row lock also serializes this decision against hiring-team
    # membership changes before any candidate is touched.
    role = require_job_permission(
        db,
        current_user=user,
        role_id=int(row.role_id),
        permission=JobPermission.CONTROL_AGENT,
    )
    row = (
        db.query(AgentNeedsInput)
        .options(joinedload(AgentNeedsInput.role))
        .filter(
            AgentNeedsInput.id == needs_input_id,
            AgentNeedsInput.organization_id == user.organization_id,
        )
        .with_for_update(of=AgentNeedsInput)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    spec = _CV_GAP_REJECT.get(row.kind)
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail="reject-cv-gap only applies to missing_cv / cv_unreadable items",
        )
    if not row.is_open:
        raise HTTPException(
            status_code=409, detail="this item is already resolved or dismissed"
        )
    cohort_fn = getattr(data_readiness, spec["cohort"])
    count_fn = getattr(data_readiness, spec["count"])

    # Pull one over the cap so we can tell "exactly at cap" from "too many".
    apps = cohort_fn(db, role=role, limit=MAX_BULK_REJECT_CV_GAP + 1)
    if len(apps) > MAX_BULK_REJECT_CV_GAP:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{MAX_BULK_REJECT_CV_GAP}+ candidates {spec['too_many']} — too many "
                f"to reject in one action. Review them on the role page and reject "
                f"in smaller batches."
            ),
        )

    from ..services.application_automation_service import reject_for_cv_gap

    org = (
        db.query(Organization)
        .filter(Organization.id == user.organization_id)
        .one_or_none()
    )

    rejected = 0
    failed: list[dict[str, Any]] = []
    for app in apps:
        # Each successful candidate commits independently because the external
        # ATS write cannot be part of our database transaction. Reacquire the
        # job lock and permission before every next effect so a hiring-team
        # removal or job deletion stops the batch at that boundary.
        role = require_job_permission(
            db,
            current_user=user,
            role_id=int(row.role_id),
            permission=JobPermission.CONTROL_AGENT,
        )
        try:
            result = reject_for_cv_gap(
                db=db,
                org=org,
                app=app,
                role=role,
                actor_type="recruiter",
                actor_id=user.id,
                reason=spec["reason"],
                trigger=f"reject_{row.kind}",
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

    # The final card-resolution write is another post-commit effect and gets
    # the same fresh authorization boundary.
    role = require_job_permission(
        db,
        current_user=user,
        role_id=int(row.role_id),
        permission=JobPermission.CONTROL_AGENT,
    )
    # Refresh/resolve both CV-gap cards against the new live counts: when this
    # card's cohort is empty it auto-resolves and disappears on the next
    # reload.
    data_readiness.sync_cv_readiness(db, role=role)
    db.commit()

    remaining = count_fn(db, role=role)
    return RejectCvGapResult(rejected=rejected, failed=failed, remaining=remaining)
