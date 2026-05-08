"""HTTP surface for the DecisionPolicy Hub.

Endpoints (admin-only, scoped to caller's organization):

  GET  /admin/decision-policy
       Active policy + revision timeline.

  GET  /admin/decision-policy/pending
       Inactive (cause='feedback_retune') policies awaiting activation,
       with diff annotations.

  POST /admin/decision-policy/{policy_id}/activate
       Activate a pending policy (deactivates the current active in
       the same transaction).

  POST /admin/decision-policy/{policy_id}/discard
       Mark a pending policy as deactivated without activating it.

  GET  /admin/decision-policy/signals
       Per-decision-point disagreement-rate timeseries + top failure
       modes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..deps import get_current_user
from ..models.agent_decision import AgentDecision
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.decision_feedback import DecisionFeedback
from ..models.decision_policy import DecisionPolicy
from ..models.rubric_revision import RubricRevision
from ..models.user import User
from ..platform.database import get_db
from .diff import policy_diff
from .engine import load_active_policy


router = APIRouter(prefix="/admin/decision-policy", tags=["decision-policy"])


def _require_admin(user: User) -> None:
    if not getattr(user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )


# ---------------------------------------------------------------------------
# Read: active policy + revision timeline
# ---------------------------------------------------------------------------


class RevisionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    cause: str
    created_at: datetime
    feedback_ids: list[int]
    notes: str | None = None
    parent_revision_id: int | None = None


class ActivePolicyView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    organization_id: int
    policy_id: int
    revision_id: int
    activated_at: datetime | None
    policy_json: dict[str, Any]
    timeline: list[RevisionSummary]


@router.get("", response_model=ActivePolicyView)
def get_active_policy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivePolicyView:
    _require_admin(user)
    org_id = int(user.organization_id)
    try:
        row = load_active_policy(db, organization_id=org_id, role_id=None)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    revisions = (
        db.query(RubricRevision)
        .filter(RubricRevision.organization_id == org_id)
        .order_by(RubricRevision.created_at.desc())
        .limit(50)
        .all()
    )
    return ActivePolicyView(
        organization_id=org_id,
        policy_id=int(row.id),
        revision_id=int(row.revision_id),
        activated_at=row.activated_at,
        policy_json=row.policy_json or {},
        timeline=[
            RevisionSummary(
                id=int(r.id),
                cause=str(r.cause),
                created_at=r.created_at,
                feedback_ids=list(r.feedback_ids or []),
                notes=r.notes,
                parent_revision_id=(
                    int(r.parent_revision_id)
                    if r.parent_revision_id is not None
                    else None
                ),
            )
            for r in revisions
        ],
    )


# ---------------------------------------------------------------------------
# Read: pending retunes (inactive feedback_retune policies)
# ---------------------------------------------------------------------------


class PendingPolicyView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    policy_id: int
    revision_id: int
    created_at: datetime
    diff: dict[str, Any]
    notes: str | None
    feedback_ids: list[int]


@router.get("/pending", response_model=list[PendingPolicyView])
def list_pending_retunes(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PendingPolicyView]:
    _require_admin(user)
    org_id = int(user.organization_id)
    try:
        active = load_active_policy(db, organization_id=org_id, role_id=None)
    except LookupError:
        active = None
    pending = (
        db.query(DecisionPolicy)
        .join(RubricRevision, RubricRevision.id == DecisionPolicy.revision_id)
        .filter(
            DecisionPolicy.organization_id == org_id,
            DecisionPolicy.activated_at.is_(None),
            DecisionPolicy.deactivated_at.is_(None),
            RubricRevision.cause == "feedback_retune",
        )
        .order_by(DecisionPolicy.created_at.desc())
        .all()
    )
    out: list[PendingPolicyView] = []
    for p in pending:
        rev = db.query(RubricRevision).filter(RubricRevision.id == p.revision_id).one()
        d = (
            policy_diff(active.policy_json or {}, p.policy_json or {})
            if active is not None
            else {}
        )
        out.append(
            PendingPolicyView(
                policy_id=int(p.id),
                revision_id=int(p.revision_id),
                created_at=p.created_at,
                diff=d,
                notes=rev.notes,
                feedback_ids=list(rev.feedback_ids or []),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Mutate: activate / discard
# ---------------------------------------------------------------------------


class ActivateResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    policy_id: int
    activated_at: datetime
    deactivated_previous: int | None


@router.post(
    "/{policy_id}/activate",
    response_model=ActivateResponse,
)
def activate_policy(
    policy_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivateResponse:
    _require_admin(user)
    org_id = int(user.organization_id)
    target = (
        db.query(DecisionPolicy)
        .filter(
            DecisionPolicy.id == policy_id,
            DecisionPolicy.organization_id == org_id,
        )
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="policy not found")
    if target.activated_at is not None:
        raise HTTPException(status_code=409, detail="policy already activated")
    if target.deactivated_at is not None:
        raise HTTPException(status_code=409, detail="policy already discarded")

    now = datetime.now(timezone.utc)
    previous = (
        db.query(DecisionPolicy)
        .filter(
            DecisionPolicy.organization_id == org_id,
            DecisionPolicy.role_id.is_(None) if target.role_id is None else (DecisionPolicy.role_id == target.role_id),
            DecisionPolicy.activated_at.isnot(None),
            DecisionPolicy.deactivated_at.is_(None),
        )
        .first()
    )
    if previous is not None:
        previous.deactivated_at = now
        db.add(previous)
    target.activated_at = now
    db.add(target)
    db.commit()
    return ActivateResponse(
        policy_id=int(target.id),
        activated_at=now,
        deactivated_previous=int(previous.id) if previous else None,
    )


@router.post("/{policy_id}/discard")
def discard_policy(
    policy_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(user)
    org_id = int(user.organization_id)
    target = (
        db.query(DecisionPolicy)
        .filter(
            DecisionPolicy.id == policy_id,
            DecisionPolicy.organization_id == org_id,
            DecisionPolicy.activated_at.is_(None),
        )
        .one_or_none()
    )
    if target is None:
        raise HTTPException(
            status_code=404, detail="pending policy not found"
        )
    target.deactivated_at = datetime.now(timezone.utc)
    db.add(target)
    db.commit()
    return {"policy_id": int(target.id), "discarded": True}


# ---------------------------------------------------------------------------
# Read: disagreement signals (per-decision-point timeseries)
# ---------------------------------------------------------------------------


class SignalsBucket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bucket_iso: str  # YYYY-MM-DD
    teach: int
    overrides: int
    manual_disagreements: int


class FailureModeCount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    failure_mode: str
    count: int


class SignalsView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    organization_id: int
    window_days: int
    buckets: list[SignalsBucket]
    top_failure_modes: list[FailureModeCount]
    manual_action_volume: int
    agent_decision_volume: int


@router.get("/signals", response_model=SignalsView)
def signals(
    days: int = 30,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SignalsView:
    _require_admin(user)
    org_id = int(user.organization_id)
    days = max(1, min(int(days), 180))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    teach_rows = (
        db.query(DecisionFeedback)
        .filter(
            DecisionFeedback.organization_id == org_id,
            DecisionFeedback.created_at >= cutoff,
        )
        .all()
    )
    override_rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == org_id,
            AgentDecision.human_disposition == "overridden",
            AgentDecision.resolved_at.isnot(None),
            AgentDecision.resolved_at >= cutoff,
        )
        .all()
    )
    manual_event_rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id == org_id,
            CandidateApplicationEvent.actor_type == "recruiter",
            CandidateApplicationEvent.created_at >= cutoff,
        )
        .all()
    )

    bucket_map: dict[str, dict[str, int]] = {}

    def _bucket(ts: datetime | None) -> str | None:
        if ts is None:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.date().isoformat()

    for r in teach_rows:
        b = _bucket(r.created_at)
        if b:
            bucket_map.setdefault(b, {"teach": 0, "overrides": 0, "manual_disagreements": 0})["teach"] += 1
    for r in override_rows:
        b = _bucket(r.resolved_at)
        if b:
            bucket_map.setdefault(b, {"teach": 0, "overrides": 0, "manual_disagreements": 0})["overrides"] += 1
    for r in manual_event_rows:
        b = _bucket(r.created_at)
        if b:
            bucket_map.setdefault(b, {"teach": 0, "overrides": 0, "manual_disagreements": 0})["manual_disagreements"] += 1

    buckets = [
        SignalsBucket(bucket_iso=k, **v) for k, v in sorted(bucket_map.items())
    ]
    failures: dict[str, int] = {}
    for r in teach_rows:
        failures[r.failure_mode] = failures.get(r.failure_mode, 0) + 1
    top_failure_modes = sorted(
        (FailureModeCount(failure_mode=fm, count=c) for fm, c in failures.items()),
        key=lambda x: -x.count,
    )[:5]

    agent_volume = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == org_id,
            AgentDecision.created_at >= cutoff,
        )
        .count()
    )

    return SignalsView(
        organization_id=org_id,
        window_days=days,
        buckets=buckets,
        top_failure_modes=top_failure_modes,
        manual_action_volume=len(manual_event_rows),
        agent_decision_volume=agent_volume,
    )
