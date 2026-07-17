"""Exact relock, drift detection, and local projection for ATS stage moves."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .ats_stage_move_claim import StageMoveClaim, _provider_snapshot
from .ats_stage_move_receipt import (
    append_stage_move_reconciliation_evidence,
    confirm_stage_move_receipt,
    reconcile_stage_move_receipt,
    stage_move_receipt,
)


@dataclass(frozen=True)
class StageMoveFinalization:
    result: dict[str, Any]
    related_note: dict[str, Any] | None = None


def _lock_finalize_scope(
    db: Session, claim: StageMoveClaim
) -> tuple[
    CandidateApplication | None,
    Candidate | None,
    Organization | None,
    Role | None,
    Role | None,
    SisterRoleEvaluation | None,
]:
    snap = claim.snapshot
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == snap.application_id,
            CandidateApplication.organization_id == snap.organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is None:
        return None, None, None, None, None, None
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == snap.expected_candidate_id,
            Candidate.organization_id == snap.organization_id,
        )
        .populate_existing()
        .with_for_update(of=Candidate)
        .one_or_none()
    )
    org = (
        db.query(Organization)
        .filter(Organization.id == snap.organization_id)
        .populate_existing()
        .with_for_update(of=Organization)
        .one_or_none()
    )
    owner = (
        db.query(Role)
        .filter(
            Role.id == snap.expected_owner_role_id,
            Role.organization_id == snap.organization_id,
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    acting = None
    evaluation = None
    if snap.acting_role_id is not None:
        acting = (
            db.query(Role)
            .filter(
                Role.id == snap.acting_role_id,
                Role.organization_id == snap.organization_id,
            )
            .populate_existing()
            .with_for_update(of=Role)
            .one_or_none()
        )
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.id == snap.related_evaluation_id,
                SisterRoleEvaluation.organization_id == snap.organization_id,
            )
            .populate_existing()
            .with_for_update(of=SisterRoleEvaluation)
            .one_or_none()
        )
    return app, candidate, org, owner, acting, evaluation


def _stage_move_drift_reason(
    db: Session,
    *,
    claim: StageMoveClaim,
    app: CandidateApplication,
    candidate: Candidate | None,
    org: Organization | None,
    owner: Role | None,
    acting: Role | None,
    evaluation: SisterRoleEvaluation | None,
) -> str | None:
    snap = claim.snapshot
    receipt = stage_move_receipt(app)
    if receipt is None:
        return "operation_receipt_missing"
    if str(receipt.get("operation_id") or "") != claim.operation_id:
        return "operation_receipt_replaced"
    if str(receipt.get("snapshot_fingerprint") or "") != snap.operation_fingerprint():
        return "operation_snapshot_changed"
    if str(receipt.get("status") or "") != "provider_succeeded":
        return "operation_receipt_not_ready"
    if app.deleted_at is not None:
        return "application_deleted"
    if int(app.version or 1) != snap.expected_application_version:
        return "application_version_changed"
    if str(app.application_outcome or "open").lower() != snap.expected_application_outcome:
        return "application_outcome_changed"
    if str(app.pipeline_stage or "applied").lower() != snap.expected_pipeline_stage:
        return "application_stage_changed"
    if bool(app.workable_disqualified) != snap.expected_workable_disqualified:
        return "application_disqualification_changed"
    if int(app.candidate_id) != snap.expected_candidate_id:
        return "application_candidate_changed"
    if int(app.role_id) != snap.expected_owner_role_id:
        return "application_role_changed"
    if candidate is None or candidate.deleted_at is not None:
        return "candidate_unavailable"
    if org is None:
        return "workspace_unavailable"
    if owner is None or owner.deleted_at is not None:
        return "owner_role_unavailable"
    if int(owner.version or 1) != snap.expected_owner_role_version:
        return "owner_role_version_changed"
    current_external_job = str(
        owner.workable_job_id
        if snap.provider == "workable"
        else owner.bullhorn_job_order_id
        or ""
    ).strip() or None
    if current_external_job != snap.owner_external_job_id:
        return "owner_provider_job_changed"
    try:
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
    except Exception:
        return "provider_authority_unavailable"
    provider, target, remote_stage, connection_key, _plan, failure = current
    if failure is not None:
        return f"provider_authority_changed:{failure[0]}"
    if provider != snap.provider:
        return "provider_changed"
    if target != snap.provider_target_id:
        return "provider_target_changed"
    if remote_stage != snap.provider_remote_stage:
        return "provider_stage_mapping_changed"
    if connection_key != snap.provider_connection_key:
        return "provider_connection_changed"
    candidate_provider_id = (
        str(candidate.bullhorn_candidate_id or "").strip() or None
        if snap.provider == "bullhorn"
        else target
    )
    if candidate_provider_id != snap.candidate_provider_id:
        return "candidate_provider_target_changed"
    if snap.acting_role_id is None:
        return None
    if acting is None or acting.deleted_at is not None:
        return "related_role_unavailable"
    if int(acting.version or 1) != int(snap.expected_acting_role_version or 0):
        return "related_role_version_changed"
    if int(acting.ats_owner_role_id or 0) != snap.expected_owner_role_id:
        return "related_role_owner_changed"
    if evaluation is None or int(evaluation.role_id) != snap.acting_role_id:
        return "related_roster_changed"
    if int(evaluation.source_application_id) != snap.application_id:
        return "related_roster_changed"
    if str(evaluation.status or "") != str(snap.related_evaluation_status or ""):
        return "related_evaluation_status_changed"
    if str(evaluation.pipeline_stage or "applied") != str(snap.related_pipeline_stage or ""):
        return "related_pipeline_stage_changed"
    if str(evaluation.spec_fingerprint or "") != str(snap.related_spec_fingerprint or ""):
        return "related_spec_changed"
    return None


def _append_reconciliation_event(
    db: Session,
    *,
    app: CandidateApplication,
    claim: StageMoveClaim,
    reason: str,
    provider_succeeded: bool | None,
) -> None:
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    append_application_event(
        db,
        app=app,
        event_type="ats_stage_move_reconciliation_required",
        actor_type="system",
        reason=(
            "The ATS stage result needs verification; concurrent local state was preserved"
        ),
        metadata={
            "operation_id": claim.operation_id,
            "provider": claim.snapshot.provider,
            "provider_target_id": claim.snapshot.provider_target_id,
            "target_stage": claim.snapshot.target_stage,
            "provider_remote_stage": claim.snapshot.provider_remote_stage,
            "drift_reason": reason,
            "provider_succeeded": provider_succeeded,
            "local_state_preserved": True,
        },
        idempotency_key=f"{claim.operation_id}:stage-reconciliation"[:200],
    )


def _reconcile_provider_success(
    db: Session,
    *,
    app: CandidateApplication,
    claim: StageMoveClaim,
    reason: str,
    remote_stage: str | None,
) -> StageMoveFinalization:
    current = stage_move_receipt(app)
    if current is not None and str(current.get("operation_id") or "") == claim.operation_id:
        reconcile_stage_move_receipt(
            app,
            operation_id=claim.operation_id,
            drift_reason=reason,
            provider_remote_stage=remote_stage,
        )
    else:
        append_stage_move_reconciliation_evidence(
            app,
            snapshot=claim.snapshot,
            operation_id=claim.operation_id,
            drift_reason=reason,
            provider_remote_stage=remote_stage,
            provider_called=True,
            provider_succeeded=True,
        )
    _append_reconciliation_event(
        db,
        app=app,
        claim=claim,
        reason=reason,
        provider_succeeded=True,
    )
    db.commit()
    return StageMoveFinalization(
        result={
            "status": "reconciliation_required",
            "application_id": claim.snapshot.application_id,
            "operation_id": claim.operation_id,
            "provider": claim.snapshot.provider,
            "reconciliation_reason": reason,
            "failed": True,
        }
    )


def _project_confirmed_stage(
    db: Session,
    *,
    app: CandidateApplication,
    owner: Role,
    acting: Role | None,
    evaluation: SisterRoleEvaluation | None,
    claim: StageMoveClaim,
    remote_stage: str | None,
) -> dict[str, Any] | None:
    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
        is_post_handover_workable_stage,
        map_legacy_status_to_pipeline,
        normalize_pipeline_key,
        transition_stage,
    )

    snap = claim.snapshot
    receipt = claim.receipt
    actor_type = str(receipt.get("actor_type") or "recruiter")
    actor_id = receipt.get("actor_id")
    source = str(receipt.get("source") or actor_type)
    reason = receipt.get("reason")
    now = datetime.now(timezone.utc)
    if snap.provider == "workable":
        app.workable_stage = snap.target_stage
        app.workable_stage_local_write_at = now
        event_type = "workable_moved"
        metadata = {
            "target_stage": snap.target_stage,
            "workable_candidate_id": snap.provider_target_id,
        }
        mapped_stage, _ = map_legacy_status_to_pipeline(snap.target_stage)
        should_advance = mapped_stage == "advanced" and is_post_handover_workable_stage(
            snap.target_stage
        )
    else:
        app.bullhorn_status = remote_stage
        app.external_stage_raw = remote_stage
        app.external_stage_normalized = normalize_pipeline_key(snap.target_intent) or None
        app.bullhorn_status_local_write_at = now
        event_type = "bullhorn_moved"
        metadata = {
            "bullhorn_status": remote_stage,
            "bullhorn_job_submission_id": snap.provider_target_id,
            "taali_intent": snap.target_intent,
        }
        should_advance = snap.target_intent in {
            "advanced",
            "advance",
            "skip_advanced",
        }
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason or f"Candidate handed back to {snap.provider.title()}",
        metadata={**metadata, "operation_id": claim.operation_id},
        idempotency_key=f"{claim.operation_id}:provider-confirmed"[:200],
    )
    if should_advance:
        transition_stage(
            db,
            app=app,
            to_stage="advanced",
            source=source,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason or f"Handed back to {snap.provider.title()}",
            metadata={"ats_provider": snap.provider, **metadata},
            idempotency_key=f"{claim.operation_id}:pipeline"[:200],
            expected_version=snap.expected_application_version,
        )
    if acting is None or evaluation is None:
        return None
    if str(evaluation.pipeline_stage or "applied").lower() == "advanced":
        return None
    from .sister_role_service import (
        related_role_advance_note,
        transition_related_role_stage,
    )

    transition_related_role_stage(evaluation, to_stage="advanced", source="recruiter")
    return {
        "status": "pending",
        "provider": snap.provider,
        "application_id": snap.application_id,
        "provider_target_id": snap.provider_target_id,
        "candidate_provider_id": snap.candidate_provider_id,
        "body": related_role_advance_note(acting, owner),
        "actor_id": actor_id,
        "dispatch_key": (
            "stage-note:"
            + hashlib.sha256(claim.operation_id.encode("utf-8")).hexdigest()
        )[:200],
    }


def finalize_stage_move_success(
    db: Session,
    *,
    claim: StageMoveClaim,
    provider_result: dict[str, Any],
) -> StageMoveFinalization:
    """Relock exact authority, then project or retain reconciliation evidence."""

    app, candidate, org, owner, acting, evaluation = _lock_finalize_scope(db, claim)
    if app is None:
        db.rollback()
        return StageMoveFinalization(
            result={
                "status": "reconciliation_required",
                "application_id": claim.snapshot.application_id,
                "operation_id": claim.operation_id,
                "reconciliation_reason": "application_unavailable",
                "failed": True,
            }
        )
    remote_stage = str(
        provider_result.get("provider_remote_stage")
        or claim.snapshot.provider_remote_stage
        or ""
    ).strip() or None
    drift = _stage_move_drift_reason(
        db,
        claim=claim,
        app=app,
        candidate=candidate,
        org=org,
        owner=owner,
        acting=acting,
        evaluation=evaluation,
    )
    if drift is not None:
        return _reconcile_provider_success(
            db, app=app, claim=claim, reason=drift, remote_stage=remote_stage
        )
    assert owner is not None
    try:
        related_note = _project_confirmed_stage(
            db,
            app=app,
            owner=owner,
            acting=acting,
            evaluation=evaluation,
            claim=claim,
            remote_stage=remote_stage,
        )
        confirm_stage_move_receipt(
            app,
            operation_id=claim.operation_id,
            provider_remote_stage=remote_stage,
            related_note=related_note,
        )
        db.commit()
    except Exception:
        db.rollback()
        relocked = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == claim.snapshot.application_id,
                CandidateApplication.organization_id == claim.snapshot.organization_id,
            )
            .populate_existing()
            .with_for_update(of=CandidateApplication)
            .one_or_none()
        )
        if relocked is None:
            db.rollback()
            raise
        return _reconcile_provider_success(
            db,
            app=relocked,
            claim=claim,
            reason="local_projection_failed",
            remote_stage=remote_stage,
        )
    return StageMoveFinalization(
        result={
            "status": "ok",
            "application_id": claim.snapshot.application_id,
            "operation_id": claim.operation_id,
            "provider": claim.snapshot.provider,
        },
        related_note=related_note,
    )



__all__ = [
    "StageMoveFinalization",
    "finalize_stage_move_success",
]
