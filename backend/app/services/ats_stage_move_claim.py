"""Short, exact claim phase for provider-backed ATS stage moves."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .application_lifecycle_restore import (
    UnresolvedProviderOperation,
    require_no_other_unresolved_provider_operation,
)
from .ats_stage_move_dispatch_snapshot import queued_stage_move_authority_failure
from .ats_stage_move_provider import StageMoveProviderPlan
from .ats_stage_move_receipt import (
    StageMoveReceiptConflict,
    StageMoveSnapshot,
    begin_stage_move_receipt,
    fail_stage_move_receipt,
    snapshot_from_stage_move_receipt,
    stage_move_receipt,
    stage_move_operation_id,
)
from .workable_actions_service import WorkableWritebackError


@dataclass(frozen=True)
class StageMoveClaim:
    snapshot: StageMoveSnapshot
    operation_id: str
    disposition: str
    provider_plan: StageMoveProviderPlan | None
    receipt: dict

def _connection_key(values: dict) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _lock_application_scope(
    db: Session, *, organization_id: int, application_id: int
) -> tuple[CandidateApplication, Candidate, Organization, Role]:
    org_id = int(organization_id)
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == org_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is None:
        raise WorkableWritebackError(
            action="move",
            code="application_unavailable",
            message="The application is no longer available for an ATS move",
            retriable=False,
        )
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(app.candidate_id),
            Candidate.organization_id == org_id,
            Candidate.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Candidate)
        .one_or_none()
    )
    organization = (
        db.query(Organization)
        .filter(Organization.id == org_id)
        .populate_existing()
        .with_for_update(of=Organization)
        .one_or_none()
    )
    owner_role = (
        db.query(Role)
        .filter(
            Role.id == int(app.role_id),
            Role.organization_id == org_id,
            Role.role_kind == ROLE_KIND_STANDARD,
            Role.ats_owner_role_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    if candidate is None or organization is None or owner_role is None:
        raise WorkableWritebackError(
            action="move",
            code="application_scope_changed",
            message="The application roster changed before the ATS move could run",
            retriable=False,
        )
    if (
        app.deleted_at is not None
        or str(app.application_outcome or "open").strip().lower() != "open"
        or bool(app.workable_disqualified)
    ):
        raise WorkableWritebackError(
            action="move",
            code="application_closed",
            message="The application closed before the ATS move could run",
            retriable=False,
        )
    return app, candidate, organization, owner_role


def _lock_related_scope(
    db: Session,
    *,
    app: CandidateApplication,
    acting_role_id: int | None,
) -> tuple[Role | None, SisterRoleEvaluation | None]:
    if acting_role_id is None:
        return None, None
    role = (
        db.query(Role)
        .filter(
            Role.id == int(acting_role_id),
            Role.organization_id == int(app.organization_id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.ats_owner_role_id == int(app.role_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    evaluation = None
    if role is not None:
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id == int(app.organization_id),
                SisterRoleEvaluation.role_id == int(role.id),
                SisterRoleEvaluation.source_application_id == int(app.id),
            )
            .populate_existing()
            .with_for_update(of=SisterRoleEvaluation)
            .one_or_none()
        )
    if role is None or evaluation is None:
        raise WorkableWritebackError(
            action="move",
            code="related_scope_unavailable",
            message="The related-role roster changed before the ATS move could run",
            retriable=False,
        )
    return role, evaluation


def _provider_snapshot(
    db: Session,
    *,
    app: CandidateApplication,
    candidate: Candidate,
    org: Organization,
    owner_role: Role,
    target_stage: str,
    target_intent: str,
    requested_provider: str | None = None,
    expected_provider_target_id: str | None = None,
) -> tuple[str, str, str | None, str, StageMoveProviderPlan | None, tuple[str, str] | None]:
    from ..platform.config import settings

    workable_target = str(app.workable_candidate_id or "").strip()
    bullhorn_target = str(app.bullhorn_job_submission_id or "").strip()
    selected = str(requested_provider or "").strip().lower()
    if selected not in {"", "workable", "bullhorn"}:
        return (
            selected,
            "",
            None,
            _connection_key({"provider": selected, "target": ""}),
            None,
            ("provider_changed", "The queued ATS provider is not supported"),
        )
    if not selected:
        if workable_target and bullhorn_target:
            return (
                "ats",
                "",
                None,
                _connection_key({"provider": "ambiguous", "target": ""}),
                None,
                (
                    "provider_ambiguous",
                    "A dual-linked stage move must name its exact queued ATS provider",
                ),
            )
        if workable_target:
            selected = "workable"
        elif bullhorn_target:
            selected = "bullhorn"
        else:
            selected = "ats"
    expected_target = str(expected_provider_target_id or "").strip()
    if selected == "workable":
        from .workable_actions_service import (
            resolved_workable_action_config,
            workable_job_syncable,
            workable_writeback_enabled,
        )

        config = resolved_workable_action_config(org, role=owner_role)
        safe = {
            "provider": "workable",
            "subdomain": str(org.workable_subdomain or "").strip().lower(),
            "actor_member_id": str(config.get("actor_member_id") or "").strip(),
            "owner_job_id": str(owner_role.workable_job_id or "").strip(),
            "writeback": bool(workable_writeback_enabled(org)),
        }
        failure = None
        if not workable_target:
            failure = ("not_linked", "The application is no longer linked to Workable")
        elif expected_target and expected_target != workable_target:
            failure = ("provider_target_changed", "The Workable target changed before the move")
        elif not target_stage:
            failure = ("missing_target_stage", "Target stage is required")
        elif not all(
            (org.workable_connected, org.workable_access_token, org.workable_subdomain)
        ):
            failure = ("not_configured", "Workable is disconnected for this application")
        elif not workable_writeback_enabled(org):
            failure = ("writeback_disabled", "Workable write-back is disabled")
        elif not config.get("has_write_scope"):
            failure = ("missing_write_scope", "Workable is missing candidate write scope")
        elif not config.get("actor_member_id"):
            failure = ("missing_actor_member_id", "Workable actor member is not configured")
        elif not workable_job_syncable(owner_role):
            failure = ("job_not_writeable", "The linked Workable job is not live")
        plan = None
        if failure is None:
            plan = StageMoveProviderPlan(
                provider="workable",
                provider_target_id=workable_target,
                target_stage=target_stage,
                provider_remote_stage=target_stage,
                organization_id=int(org.id),
                workable_subdomain=str(org.workable_subdomain),
                workable_actor_member_id=str(config["actor_member_id"]),
                workable_access_token=str(org.workable_access_token),
            )
        return (
            "workable",
            workable_target,
            target_stage,
            _connection_key(safe),
            plan,
            failure,
        )
    if selected == "bullhorn":
        from ..components.integrations.bullhorn import write_back

        remote_stage = write_back.resolve_remote_status(
            db, org, taali_intent=target_intent
        )
        safe = {
            "provider": "bullhorn",
            "credential_generation": int(org.bullhorn_credential_generation or 0),
            "client_id": str(org.bullhorn_client_id or ""),
            "owner_job_id": str(owner_role.bullhorn_job_order_id or ""),
            "remote_stage": str(remote_stage or ""),
        }
        failure = None
        if not bullhorn_target:
            failure = ("not_linked", "The application is no longer linked to Bullhorn")
        elif expected_target and expected_target != bullhorn_target:
            failure = ("provider_target_changed", "The Bullhorn target changed before the move")
        elif not target_intent:
            failure = ("missing_target_stage", "Target stage is required")
        elif not settings.BULLHORN_ENABLED or not all(
            (
                org.bullhorn_connected,
                org.bullhorn_username,
                org.bullhorn_client_id,
                org.bullhorn_client_secret,
                org.bullhorn_refresh_token,
            )
        ):
            failure = ("not_configured", "Bullhorn is disconnected for this application")
        elif not bullhorn_target.isdigit():
            failure = ("not_linked", "The Bullhorn JobSubmission target is invalid")
        elif not remote_stage:
            failure = ("needs_mapping", "No exact Bullhorn status is mapped for this move")
        plan = None
        if failure is None:
            plan = StageMoveProviderPlan(
                provider="bullhorn",
                provider_target_id=bullhorn_target,
                target_stage=target_intent,
                provider_remote_stage=remote_stage,
                organization_id=int(org.id),
                bullhorn_username=str(org.bullhorn_username),
                bullhorn_client_id=str(org.bullhorn_client_id),
                bullhorn_client_secret=str(org.bullhorn_client_secret),
                bullhorn_refresh_token=str(org.bullhorn_refresh_token),
                bullhorn_rest_url=str(org.bullhorn_rest_url or "") or None,
                bullhorn_credential_generation=int(
                    org.bullhorn_credential_generation or 0
                ),
            )
        return (
            "bullhorn",
            bullhorn_target,
            remote_stage,
            _connection_key(safe),
            plan,
            failure,
        )
    return (
        "ats",
        "",
        None,
        _connection_key({"provider": "ats", "target": ""}),
        None,
        ("not_linked", "The application is no longer linked to a writable ATS"),
    )


def claim_stage_move(
    db: Session, *, organization_id: int, payload: dict
) -> StageMoveClaim:
    """Persist an exact provider boundary, commit it, and return primitives."""

    app, candidate, org, owner = _lock_application_scope(
        db,
        organization_id=int(organization_id),
        application_id=int(payload["application_id"]),
    )
    acting_id = (
        int(payload["acting_role_id"])
        if payload.get("acting_role_id") is not None
        else None
    )
    acting, evaluation = _lock_related_scope(
        db, app=app, acting_role_id=acting_id
    )
    target_stage = str(payload.get("target_stage") or "").strip()
    target_intent = str(payload.get("target_intent") or target_stage).strip().lower()
    provider, provider_target, remote_stage, connection_key, plan, failure = (
        _provider_snapshot(
            db,
            app=app,
            candidate=candidate,
            org=org,
            owner_role=owner,
            target_stage=target_stage,
            target_intent=target_intent,
            requested_provider=payload.get("provider"),
            expected_provider_target_id=payload.get("provider_target_id"),
        )
    )
    owner_external_job_id = str(
        owner.workable_job_id if provider == "workable" else owner.bullhorn_job_order_id
        or ""
    ).strip() or None
    snapshot = StageMoveSnapshot(
        organization_id=int(org.id),
        application_id=int(app.id),
        expected_application_version=int(app.version or 1),
        expected_application_outcome=str(app.application_outcome or "open").lower(),
        expected_pipeline_stage=str(app.pipeline_stage or "applied").lower(),
        expected_workable_disqualified=bool(app.workable_disqualified),
        expected_candidate_id=int(candidate.id),
        expected_owner_role_id=int(owner.id),
        expected_owner_role_version=int(owner.version or 1),
        provider=provider,
        provider_target_id=provider_target,
        target_stage=target_stage,
        target_intent=target_intent,
        provider_remote_stage=remote_stage,
        owner_external_job_id=owner_external_job_id,
        provider_connection_key=connection_key,
        acting_role_id=int(acting.id) if acting is not None else None,
        expected_acting_role_version=(
            int(acting.version or 1) if acting is not None else None
        ),
        related_evaluation_id=(int(evaluation.id) if evaluation is not None else None),
        related_evaluation_status=(
            str(evaluation.status or "") if evaluation is not None else None
        ),
        related_pipeline_stage=(
            str(evaluation.pipeline_stage or "applied") if evaluation is not None else None
        ),
        related_spec_fingerprint=(
            str(evaluation.spec_fingerprint or "") if evaluation is not None else None
        ),
        candidate_provider_id=(
            str(candidate.bullhorn_candidate_id or "").strip() or None
            if provider == "bullhorn"
            else provider_target
        ),
    )
    operation_id = stage_move_operation_id(
        snapshot,
        payload.get("operation_id") or payload.get("stage_move_operation_id"),
    )
    current = stage_move_receipt(app)
    if current is not None and str(current.get("operation_id") or "") == operation_id:
        try:
            receipt_snapshot = snapshot_from_stage_move_receipt(current)
        except (TypeError, ValueError):
            failure = (
                "operation_snapshot_invalid",
                "The durable ATS stage-move authority snapshot is invalid",
            )
        else:
            status = str(current.get("status") or "").strip().lower()
            if status in {
                "confirmed",
                "provider_call_started",
                "provider_succeeded",
                "manual_reconciliation_required",
            }:
                # Replay/finalization belongs to the original pre-provider
                # snapshot. Its own successful projection may have advanced
                # local fields, which must not turn an exact replay into a new
                # operation or another provider call.
                snapshot = receipt_snapshot
            elif snapshot.operation_fingerprint() != receipt_snapshot.operation_fingerprint():
                failure = (
                    "queued_authority_changed",
                    "Application authority changed before the ATS stage move could be safely rearmed",
                )
    try:
        require_no_other_unresolved_provider_operation(
            app,
            receipt_key="stage_move_operation",
            operation_id=operation_id,
        )
    except UnresolvedProviderOperation as exc:
        db.rollback()
        raise WorkableWritebackError(
            action="move",
            code="provider_operation_in_progress",
            message=str(exc),
            retriable=False,
        ) from None
    failure = failure or queued_stage_move_authority_failure(payload, snapshot)
    try:
        receipt, disposition = begin_stage_move_receipt(
            app,
            snapshot=snapshot,
            operation_id=operation_id,
            job_run_id=payload.get("_job_run_id"),
            actor_type=str(payload.get("actor_type") or "recruiter"),
            actor_id=payload.get("actor_id", payload.get("user_id")),
            reason=payload.get("reason"),
        )
    except StageMoveReceiptConflict as exc:
        db.rollback()
        raise WorkableWritebackError(
            action="move",
            code="stage_move_conflict",
            message=str(exc),
            retriable=False,
        ) from None
    if failure is not None and disposition == "call_provider":
        fail_stage_move_receipt(
            app,
            operation_id=operation_id,
            error_code=failure[0],
            error_message=failure[1],
            provider_called=False,
            retryable=False,
        )
        db.commit()
        raise WorkableWritebackError(
            action="move",
            code=failure[0],
            message=failure[1],
            retriable=False,
        )
    db.commit()
    # No lazy ORM read is allowed below this point. The provider plan contains
    # only copied primitives, and commit returned the pooled connection.
    return StageMoveClaim(
        snapshot=snapshot,
        operation_id=operation_id,
        disposition=disposition,
        provider_plan=(plan if disposition == "call_provider" else None),
        receipt=receipt,
    )


__all__ = ["StageMoveClaim", "claim_stage_move"]
