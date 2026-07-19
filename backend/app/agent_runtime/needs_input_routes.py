"""HTTP surface for agent_needs_input rows.

Recruiters answer the agent's open questions inline on the role page.
Each route is org-scoped; only the current user's org rows are visible.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, joinedload

from ..actions import ask_recruiter as ask_recruiter_action
from ..actions.types import Actor
from ..agent_chat.recruiter_inputs import recruiter_input_allows_dismiss
from ..deps import get_current_user
from ..domains.assessments_runtime.role_support import (
    role_family_response,
    roles_with_families,
)
from ..models.agent_needs_input import AgentNeedsInput
from ..models.role import Role
from ..models.user import User
from ..platform.database import get_db
from ..schemas.role import RoleFamilyResponse
from ..services.cv_gap_rejection_authority import (
    CV_GAP_REJECTION_SPECS,
    cv_gap_rejection_preview,
)
from .cv_gap_rejection_contracts import (
    CvGapRejectPreview,
    RejectCvGapAccepted,
)
from .cv_gap_rejection_routes import get_reject_cv_gap_preview, reject_cv_gap

logger = logging.getLogger("taali.agent_runtime.needs_input_routes")

router = APIRouter(prefix="/agent-needs-input", tags=["agent-needs-input"])
router.add_api_route(
    "/{needs_input_id}/reject-cv-gap-preview",
    get_reject_cv_gap_preview,
    methods=["GET"],
    response_model=CvGapRejectPreview,
)
router.add_api_route(
    "/{needs_input_id}/reject-cv-gap",
    reject_cv_gap,
    methods=["POST"],
    response_model=RejectCvGapAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)


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
    role_family: RoleFamilyResponse | None = None
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
    # Present only for an open CV-gap card. This is the complete proof the
    # destructive mutation must echo; the worker never discovers extra IDs.
    cv_gap_rejection: CvGapRejectPreview | None = None

    @classmethod
    def from_row(
        cls,
        row: AgentNeedsInput,
        *,
        cv_gap_rejection: dict[str, Any] | None = None,
    ) -> "NeedsInputView":
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
            role_family=(
                role_family_response(row.role) if row.role is not None else None
            ),
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
            cv_gap_rejection=cv_gap_rejection,
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
    # Populate complete, org-scoped family references in one batch so every
    # destructive CV-gap shortcut can name the full blast radius without an
    # N+1 relationship walk.
    roles_with_families(
        db,
        [int(row.role_id) for row in rows],
        organization_id=int(user.organization_id),
    )
    preview_cache: dict[tuple[int, str], dict[str, Any] | None] = {}
    result: list[NeedsInputView] = []
    for row in rows:
        preview = None
        if row.is_open and row.kind in CV_GAP_REJECTION_SPECS:
            key = (int(row.role_id), str(row.kind))
            if key not in preview_cache:
                preview_cache[key] = cv_gap_rejection_preview(
                    db,
                    organization_id=int(user.organization_id),
                    role_id=int(row.role_id),
                    kind=str(row.kind),
                )
            preview = preview_cache[key]
        result.append(NeedsInputView.from_row(row, cv_gap_rejection=preview))
    return result


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
