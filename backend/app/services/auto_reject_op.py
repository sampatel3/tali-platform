"""Short-row-lock ATS operation for deterministic pre-screen rejection."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .workable_actions_service import WorkableWritebackError

AUTO_REJECT_OP = "auto_reject"

# These fail before provider I/O. Every other terminal error records an unknown
# outcome because the ATS may have received a request whose response was lost.
_PROVIDER_NOT_CALLED_FAILURE_CODES = frozenset({
    "missing_actor_member_id", "missing_candidate_id", "missing_connection",
    "missing_submission_id", "missing_write_scope", "needs_mapping",
    "not_configured", "not_writeable", "writeback_disabled",
})


def lock_auto_reject_context(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    require_live_role: bool,
) -> tuple[CandidateApplication | None, Organization | None, Role | None]:
    """Lock Application -> Organization -> Role, in that exact order."""

    from .role_execution_guard import lock_live_role
    from .workspace_agent_control import workspace_agent_control_snapshot

    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is None:
        return None, None, None
    if require_live_role:
        # lock_live_role acquires the Organization row, flushes, then Role.
        role = lock_live_role(
            db,
            role_id=int(app.role_id),
            organization_id=int(organization_id),
        )
    else:
        workspace_agent_control_snapshot(
            db, organization_id=int(organization_id), lock=True
        )
        role = (
            db.query(Role)
            .filter(
                Role.id == int(app.role_id),
                Role.organization_id == int(organization_id),
            )
            .populate_existing()
            .with_for_update(of=Role)
            .one_or_none()
        )
    org = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .populate_existing()
        .one_or_none()
    )
    return app, org, role


def execute_auto_reject_op(
    db: Session, organization_id: int, payload: dict
) -> dict:
    """Claim under row locks, call ATS lock-free, then reconcile under locks."""

    from ..domains.assessments_runtime.pipeline_service import append_application_event
    from .application_automation_service import run_auto_reject_if_needed
    from .ats_outcome_provider import (
        bullhorn_outcome_provider_plan,
        perform_outcome_provider_call,
        workable_outcome_provider_plan,
    )
    from .auto_reject_deferred import finalize_deferred_auto_reject_success
    from .auto_reject_operation_receipt import (
        authorize_auto_reject_operation,
        auto_reject_operation_drift_reason,
        cancel_auto_reject_before_provider,
        mark_auto_reject_provider_call_started,
        surface_auto_reject_manual_reconciliation,
    )
    from .role_execution_guard import automatic_role_action_block_reason

    application_id = int(payload["application_id"])
    actor_type = str(payload.get("actor_type") or "auto")[:32]
    receipt_key = str(payload.get("receipt_key") or "").strip() or None
    app, org, role = lock_auto_reject_context(
        db,
        organization_id=int(organization_id),
        application_id=application_id,
        require_live_role=True,
    )
    if app is None:
        db.rollback()
        return _skip(application_id, "not_found")
    if app.deleted_at is not None:
        db.rollback()
        return _skip(application_id, "application_deleted")
    if (app.pipeline_stage or "").strip().lower() == "sourced":
        db.rollback()
        return _skip(application_id, "sourced_prospect")
    if str(app.application_outcome or "open").strip().lower() != "open":
        db.rollback()
        return _skip(application_id, "application_closed")
    if org is None or role is None:
        db.rollback()
        return _skip(application_id, "role_or_workspace_unavailable")

    execution_block = automatic_role_action_block_reason(role, db=db)
    decision = run_auto_reject_if_needed(
        db=db,
        org=org,
        app=app,
        role=role,
        actor_type=actor_type,
        defer_provider_writeback=True,
        receipt_key=receipt_key,
        allow_irreversible_execution=execution_block is None,
    )
    if not decision.get("provider_writeback_required"):
        db.commit()
        return {
            "status": "ok",
            "application_id": application_id,
            "performed": bool(decision.get("performed")),
            "state": decision.get("state"),
            "reason": execution_block or decision.get("reason"),
        }

    decision = authorize_auto_reject_operation(
        app=app,
        organization=org,
        role=role,
        decision=decision,
        receipt_key=receipt_key,
    )
    provider_name = str(decision["provider"])
    operation_id = str(decision["operation_id"])
    # Durable authorization/in-flight receipt; releases all three row locks.
    db.commit()

    # Reacquire the canonical rows and prove that the exact local outcome and
    # execution authority captured by the durable receipt still hold.  The
    # commit below deliberately releases every lock before provider I/O.
    provider_app, provider_org, provider_role = lock_auto_reject_context(
        db,
        organization_id=int(organization_id),
        application_id=application_id,
        require_live_role=False,
    )
    drift_reason = auto_reject_operation_drift_reason(
        db,
        app=provider_app,
        organization=provider_org,
        role=provider_role,
        decision=decision,
    )
    if drift_reason:
        if provider_app is not None:
            cancel_auto_reject_before_provider(
                db,
                app=provider_app,
                decision=decision,
                drift_reason=drift_reason,
                actor_type=actor_type,
            )
            db.commit()
        else:
            db.rollback()
        return _cancelled_before_provider(application_id, drift_reason)
    assert provider_app is not None
    mark_auto_reject_provider_call_started(
        provider_app,
        operation_id=operation_id,
    )
    db.commit()

    provider_app, provider_org, provider_role = _read_provider_context(
        db, organization_id=int(organization_id), application_id=application_id
    )
    drift_reason = auto_reject_operation_drift_reason(
        db,
        app=provider_app,
        organization=provider_org,
        role=provider_role,
        decision=decision,
    )
    if drift_reason:
        # The unlocked provider-input read may observe a mutation committed
        # immediately after the locked preflight.  Relock before recording the
        # safe, provider-free cancellation.
        db.rollback()
        cancelled_app, cancelled_org, cancelled_role = lock_auto_reject_context(
            db,
            organization_id=int(organization_id),
            application_id=application_id,
            require_live_role=False,
        )
        locked_drift_reason = auto_reject_operation_drift_reason(
            db,
            app=cancelled_app,
            organization=cancelled_org,
            role=cancelled_role,
            decision=decision,
        )
        if cancelled_app is not None:
            cancel_auto_reject_before_provider(
                db,
                app=cancelled_app,
                decision=decision,
                drift_reason=locked_drift_reason or drift_reason,
                actor_type=actor_type,
            )
            db.commit()
        else:
            db.rollback()
        return _cancelled_before_provider(
            application_id,
            locked_drift_reason or drift_reason,
        )

    assert provider_app is not None
    assert provider_org is not None

    if provider_name == "bullhorn":
        provider_plan = bullhorn_outcome_provider_plan(
            db,
            org=provider_org,
            app=provider_app,
            target_outcome="rejected",
        )
    else:
        config = decision.get("config") or {}
        provider_plan = workable_outcome_provider_plan(
            org=provider_org,
            app=provider_app,
            role=provider_role,
            target_outcome="rejected",
            reason=decision.get("reason"),
            note_template=config.get("auto_reject_note_template"),
            threshold_100=config.get("threshold_100"),
        )
    db.rollback()
    assert not db.in_transaction()
    provider_result = perform_outcome_provider_call(provider_plan)
    provider_result = dict(provider_result or {})
    if isinstance(provider_result.get("config"), dict):
        provider_result["config"] = dict(provider_result["config"])
    if provider_name == "bullhorn":
        provider_result.setdefault("config", {})["remote_status"] = str(
            provider_result.get("provider_remote_stage") or ""
        ).strip()
    if not provider_result.get("success"):
        code = str(provider_result.get("code") or "api_error")
        raise WorkableWritebackError(
            action=AUTO_REJECT_OP,
            code=code,
            message=str(
                provider_result.get("message")
                or f"{provider_name.title()} did not confirm the rejection"
            ),
            retriable=code == "api_error",
        )
    final_app, final_org, final_role = lock_auto_reject_context(
        db,
        organization_id=int(organization_id),
        application_id=application_id,
        require_live_role=False,
    )
    if final_app is None:
        db.rollback()
        return {
            "status": "manual_reconciliation_required",
            "application_id": application_id,
            "performed": False,
            "provider_performed": True,
            "provider": provider_name,
            "state": "manual_reconciliation_required",
            "reason": (
                "ATS rejection succeeded, but the local application became "
                "unavailable; manual provider reconciliation is required"
            ),
            "drift_reason": "local_reconciliation_unavailable",
        }
    drift_reason = auto_reject_operation_drift_reason(
        db,
        app=final_app,
        organization=final_org,
        role=final_role,
        decision=decision,
    )
    if drift_reason:
        reconciled = surface_auto_reject_manual_reconciliation(
            db,
            app=final_app,
            decision=decision,
            provider=provider_name,
            provider_result=provider_result,
            drift_reason=drift_reason,
            actor_type=actor_type,
        )
        db.commit()
        return {
            "status": "manual_reconciliation_required",
            "application_id": application_id,
            "performed": False,
            "provider_performed": True,
            "provider": provider_name,
            "state": reconciled.get("state"),
            "reason": reconciled.get("reason"),
            "drift_reason": drift_reason,
        }
    finalized = finalize_deferred_auto_reject_success(
        db,
        app=final_app,
        role=final_role,
        decision=decision,
        provider=provider_name,
        provider_result=provider_result,
        actor_type=actor_type,
        receipt_key=receipt_key,
    )
    append_application_event(
        db,
        app=final_app,
        event_type="workable_auto_reject_applied",
        actor_type=actor_type,
        reason=str(finalized.get("reason") or "Auto reject applied"),
        metadata={
            "ats_provider": provider_name,
            "pre_screen_score": (finalized.get("snapshot") or {}).get(
                "pre_screen_score"
            ),
            "threshold_100": (finalized.get("config") or {}).get("threshold_100"),
        },
        idempotency_key=(f"{receipt_key}:applied" if receipt_key else None),
    )
    db.commit()
    return {
        "status": "ok",
        "application_id": application_id,
        "performed": True,
        "provider": provider_name,
    }


def surface_auto_reject_failure(
    db: Session,
    *,
    organization_id: int,
    payload: dict,
    error: WorkableWritebackError,
) -> None:
    """Card a terminal provider failure under canonical locks."""

    from .auto_reject_deferred import surface_deferred_auto_reject_failure
    from .auto_reject_operation_receipt import (
        auto_reject_operation_receipt,
        mark_auto_reject_terminal_failure,
    )

    application_id = int(payload["application_id"])
    app, org, role = lock_auto_reject_context(
        db,
        organization_id=int(organization_id),
        application_id=application_id,
        require_live_role=False,
    )
    if app is None:
        db.rollback()
        return
    operation_id = str(payload.get("receipt_key") or "").strip()
    active_receipt = auto_reject_operation_receipt(app)
    receipt_provider = ""
    if (
        active_receipt is not None
        and str(active_receipt.get("operation_id") or "") == operation_id
    ):
        receipt_provider = str(active_receipt.get("provider") or "").strip().lower()
    failed_receipt = mark_auto_reject_terminal_failure(
        app,
        operation_id=operation_id,
        error_code=error.code,
        error_message=error.message,
        provider_called=getattr(
            error,
            "provider_called",
            (
                False
                if str(error.code or "").strip().lower()
                in _PROVIDER_NOT_CALLED_FAILURE_CODES
                else None
            ),
        ),
    )
    # A delayed callback for a superseded/replaced operation must not mutate or
    # card the fresh lifecycle. The durable exact-operation transition above is
    # the authority to surface this failure.
    if failed_receipt is None:
        db.rollback()
        return
    provider = (
        receipt_provider
        if receipt_provider in {"bullhorn", "workable"}
        else (
            "bullhorn"
            if app.bullhorn_job_submission_id and not app.workable_candidate_id
            else "workable"
        )
    )
    surface_deferred_auto_reject_failure(
        db,
        app=app,
        org=org,
        role=role,
        provider=provider,
        error_code=error.code,
        error_message=error.message,
        actor_type=str(payload.get("actor_type") or "auto")[:32],
        receipt_key=operation_id or None,
        provider_outcome_uncertain=bool(
            failed_receipt.get("provider_outcome_uncertain")
        ),
    )
    db.commit()


def _read_provider_context(
    db: Session, *, organization_id: int, application_id: int
) -> tuple[CandidateApplication | None, Organization | None, Role | None]:
    """Read provider inputs after the claim commit, without row locks."""

    db.expire_all()
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    org = db.get(Organization, int(organization_id))
    role = db.get(Role, int(app.role_id)) if app is not None else None
    return app, org, role


def _skip(application_id: int, reason: str) -> dict:
    return {
        "status": "skipped",
        "reason": reason,
        "application_id": int(application_id),
    }


def _cancelled_before_provider(application_id: int, reason: str) -> dict:
    return {
        "status": "skipped",
        "reason": reason,
        "application_id": int(application_id),
        "performed": False,
        "provider_performed": False,
    }


def _failure(code: str, message: str) -> WorkableWritebackError:
    return WorkableWritebackError(
        action=AUTO_REJECT_OP,
        code=code,
        message=message,
        retriable=False,
    )


__all__ = [
    "AUTO_REJECT_OP",
    "execute_auto_reject_op",
    "lock_auto_reject_context",
    "surface_auto_reject_failure",
]
