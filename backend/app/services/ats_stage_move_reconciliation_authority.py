"""Fresh locked authority validation for stage-move reconciliation."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .ats_stage_move_claim import _provider_snapshot
from .ats_stage_move_provider import (
    StageMoveObservationPlan,
    stage_move_observation_plan,
)
from .ats_stage_move_receipt import (
    StageMoveSnapshot,
    snapshot_from_stage_move_receipt,
)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def lock_stage_reconciliation_authority(
    db: Session,
    *,
    app: CandidateApplication,
    receipt: dict,
) -> tuple[StageMoveSnapshot, StageMoveObservationPlan]:
    """Revalidate every frozen local/provider field under fresh row locks."""

    try:
        snap = snapshot_from_stage_move_receipt(receipt)
    except (TypeError, ValueError):
        raise _conflict("The stage-move receipt lacks an exact authority snapshot") from None
    if any(
        (
            app.deleted_at is not None,
            int(app.organization_id) != int(snap.organization_id),
            int(app.id) != int(snap.application_id),
            int(app.version or 1) != int(snap.expected_application_version),
            str(app.application_outcome or "open").lower()
            != str(snap.expected_application_outcome),
            str(app.pipeline_stage or "applied").lower()
            != str(snap.expected_pipeline_stage),
            bool(app.workable_disqualified) != bool(snap.expected_workable_disqualified),
            int(app.candidate_id) != int(snap.expected_candidate_id),
            int(app.role_id) != int(snap.expected_owner_role_id),
        )
    ):
        raise _conflict("Application authority changed after the ATS stage move")
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(snap.expected_candidate_id),
            Candidate.organization_id == int(snap.organization_id),
            Candidate.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Candidate)
        .one_or_none()
    )
    org = (
        db.query(Organization)
        .filter(Organization.id == int(snap.organization_id))
        .populate_existing()
        .with_for_update(of=Organization)
        .one_or_none()
    )
    owner = (
        db.query(Role)
        .filter(
            Role.id == int(snap.expected_owner_role_id),
            Role.organization_id == int(snap.organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    if candidate is None or org is None or owner is None:
        raise _conflict("Candidate, workspace, or owner authority is unavailable")
    if int(owner.version or 1) != int(snap.expected_owner_role_version):
        raise _conflict("Owner-role authority changed after the ATS stage move")
    external_job = str(
        owner.workable_job_id
        if snap.provider == "workable"
        else owner.bullhorn_job_order_id or ""
    ).strip() or None
    if external_job != snap.owner_external_job_id:
        raise _conflict("The exact ATS owner job changed after the stage move")
    if snap.acting_role_id is not None:
        acting = (
            db.query(Role)
            .filter(
                Role.id == int(snap.acting_role_id),
                Role.organization_id == int(snap.organization_id),
                Role.deleted_at.is_(None),
            )
            .populate_existing()
            .with_for_update(of=Role)
            .one_or_none()
        )
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.id == int(snap.related_evaluation_id or 0),
                SisterRoleEvaluation.organization_id == int(snap.organization_id),
                SisterRoleEvaluation.source_application_id == int(snap.application_id),
            )
            .populate_existing()
            .with_for_update(of=SisterRoleEvaluation)
            .one_or_none()
        )
        if (
            acting is None
            or evaluation is None
            or int(acting.version or 1) != int(snap.expected_acting_role_version or 0)
            or int(acting.ats_owner_role_id or 0) != int(snap.expected_owner_role_id)
            or int(evaluation.role_id) != int(snap.acting_role_id)
            or str(evaluation.status or "") != str(snap.related_evaluation_status or "")
            or str(evaluation.pipeline_stage or "applied")
            != str(snap.related_pipeline_stage or "")
            or str(evaluation.spec_fingerprint or "")
            != str(snap.related_spec_fingerprint or "")
        ):
            raise _conflict("Related-role authority changed after the ATS stage move")
    current = _provider_snapshot(
        db,
        app=app,
        candidate=candidate,
        org=org,
        owner_role=owner,
        target_stage=snap.target_stage,
        target_intent=snap.target_intent,
        requested_provider=snap.provider,
        expected_provider_target_id=snap.provider_target_id,
    )
    provider, target, remote_stage, connection_key, plan, failure = current
    if (
        failure is not None
        or plan is None
        or provider != snap.provider
        or target != snap.provider_target_id
        or remote_stage != snap.provider_remote_stage
        or connection_key != snap.provider_connection_key
    ):
        raise _conflict("Exact ATS provider, target, mapping, or connection changed")
    candidate_target = (
        str(candidate.bullhorn_candidate_id or "").strip() or None
        if snap.provider == "bullhorn"
        else target
    )
    if candidate_target != snap.candidate_provider_id:
        raise _conflict("The exact ATS candidate target changed")
    return snap, stage_move_observation_plan(plan)


__all__ = ["lock_stage_reconciliation_authority"]
