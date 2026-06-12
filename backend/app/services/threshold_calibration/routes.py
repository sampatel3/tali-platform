"""Admin review surface for learned role_fit threshold proposals.

A proposal is shadow (never read by the engine) until a recruiter activates it
here — at which point ``resolve_role_fit_threshold`` starts returning it. List
active calibrations, list pending proposals (with the diff vs the currently
effective threshold), activate, or discard. Mirrors the decision-policy admin
routes (admin-only, org-scoped).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role import Role
from ...models.threshold_calibration import (
    STATUS_ACTIVE,
    STATUS_DISCARDED,
    STATUS_PROPOSED,
    STATUS_SUPERSEDED,
    ThresholdCalibration,
)
from ...models.user import User
from ...platform.database import get_db
from ...services.auto_threshold_service import (
    compute_role_fit_send_threshold,
    resolve_role_fit_threshold,
)
from . import service as svc

router = APIRouter(prefix="/admin/threshold-calibration", tags=["threshold-calibration"])


def _require_admin(user: User) -> None:
    if not getattr(user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )


class CalibrationView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    scope: str
    role_id: int | None
    learned_threshold: float
    status: str
    metric_name: str
    metric_value: float | None
    balanced_accuracy: float | None = None
    n_positive: int
    n_negative: int
    pooled_from_org: bool
    bias_gate_passed: bool | None
    bias_gate_cold_start: bool | None
    bias_gate_reason: str | None
    proposed_at: datetime | None
    activated_at: datetime | None


class PendingView(CalibrationView):
    # The threshold currently in effect for this scope, and the proposed move.
    current_effective: float | None = None
    delta: float | None = None


def _view(row: ThresholdCalibration, *, current: float | None = None):
    metrics = row.metrics_json if isinstance(row.metrics_json, dict) else {}
    base = dict(
        id=row.id, scope=row.scope, role_id=row.role_id,
        learned_threshold=row.learned_threshold, status=row.status,
        metric_name=row.metric_name, metric_value=row.metric_value,
        balanced_accuracy=metrics.get("balanced_accuracy"),
        n_positive=row.n_positive, n_negative=row.n_negative,
        pooled_from_org=row.pooled_from_org, bias_gate_passed=row.bias_gate_passed,
        bias_gate_cold_start=row.bias_gate_cold_start, bias_gate_reason=row.bias_gate_reason,
        proposed_at=row.proposed_at, activated_at=row.activated_at,
    )
    if current is not None:
        return PendingView(
            **base, current_effective=current,
            delta=round(row.learned_threshold - current, 2),
        )
    return CalibrationView(**base)


def _get_owned(db: Session, calibration_id: int, org_id: int) -> ThresholdCalibration:
    row = (
        db.query(ThresholdCalibration)
        .filter(
            ThresholdCalibration.id == calibration_id,
            ThresholdCalibration.organization_id == org_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="calibration not found")
    return row


@router.get("", response_model=list[CalibrationView])
def list_active(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _require_admin(user)
    rows = (
        db.query(ThresholdCalibration)
        .filter(
            ThresholdCalibration.organization_id == int(user.organization_id),
            ThresholdCalibration.status == STATUS_ACTIVE,
        )
        .order_by(ThresholdCalibration.activated_at.desc())
        .all()
    )
    return [_view(r) for r in rows]


@router.get("/pending", response_model=list[PendingView])
def list_pending(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _require_admin(user)
    org_id = int(user.organization_id)
    rows = (
        db.query(ThresholdCalibration)
        .filter(
            ThresholdCalibration.organization_id == org_id,
            ThresholdCalibration.status == STATUS_PROPOSED,
        )
        .order_by(ThresholdCalibration.proposed_at.desc())
        .all()
    )
    out = []
    for r in rows:
        current = None
        try:
            if r.role_id is not None:
                role = db.query(Role).filter(Role.id == r.role_id).first()
                if role is not None:
                    current = resolve_role_fit_threshold(db, role=role)
            else:
                role = db.query(Role).filter(Role.organization_id == org_id).first()
                if role is not None:
                    current = float(compute_role_fit_send_threshold(db, role=role).value)
        except Exception:
            current = None
        out.append(_view(r, current=current))
    return out


@router.post("/{calibration_id}/activate", response_model=CalibrationView)
def activate_calibration(
    calibration_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    row = _get_owned(db, calibration_id, int(user.organization_id))
    if row.status == STATUS_ACTIVE:
        raise HTTPException(status_code=409, detail="already active")
    if row.status in (STATUS_DISCARDED, STATUS_SUPERSEDED):
        raise HTTPException(status_code=409, detail=f"cannot activate a {row.status} proposal")
    svc.activate(db, row)
    db.commit()
    db.refresh(row)
    return _view(row)


@router.post("/{calibration_id}/discard", response_model=CalibrationView)
def discard_calibration(
    calibration_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    row = _get_owned(db, calibration_id, int(user.organization_id))
    if row.status != STATUS_PROPOSED:
        raise HTTPException(status_code=409, detail=f"cannot discard a {row.status} proposal")
    row.status = STATUS_DISCARDED
    db.commit()
    db.refresh(row)
    return _view(row)
