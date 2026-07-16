"""Short-row-lock ATS operation for deterministic pre-screen rejection."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .workable_actions_service import (
    WorkableWritebackError,
    strict_workable_writes,
)

AUTO_REJECT_OP = "auto_reject"


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

    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider
    from ..domains.assessments_runtime.pipeline_service import append_application_event
    from .application_automation_service import run_auto_reject_if_needed
    from .auto_reject_deferred import finalize_deferred_auto_reject_success
    from .role_execution_guard import automatic_role_action_block_reason
    from .workable_actions_service import disqualify_candidate_in_workable

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

    provider_name = str(decision["provider"])
    provider_target_id = str(decision["provider_target_id"])
    # Durable authorization/in-flight receipt; releases all three row locks.
    db.commit()

    provider_app, provider_org, provider_role = _read_provider_context(
        db, organization_id=int(organization_id), application_id=application_id
    )
    if provider_app is None or provider_org is None:
        raise _failure(
            "local_context_unavailable",
            "Application or organization became unavailable before ATS write-back",
        )
    current_target = str(
        (
            provider_app.bullhorn_job_submission_id
            if provider_name == "bullhorn"
            else provider_app.workable_candidate_id
        )
        or ""
    ).strip()
    if current_target != provider_target_id:
        raise _failure(
            "provider_target_changed",
            "Application ATS linkage changed before auto-reject write-back",
        )

    with strict_workable_writes():
        if provider_name == "bullhorn":
            provider = resolve_application_ats_provider(
                provider_org, db, provider_app
            )
            if not isinstance(provider, BullhornProvider):
                raise _failure(
                    "not_configured",
                    "Bullhorn is disabled or disconnected for this application",
                )
            provider_result = provider.reject_application(
                app=provider_app,
                role=provider_role,
                reason=decision.get("reason"),
            )
        else:
            config = decision.get("config") or {}
            provider_result = disqualify_candidate_in_workable(
                org=provider_org,
                app=provider_app,
                role=provider_role,
                reason=decision.get("reason"),
                note_template=config.get("auto_reject_note_template"),
                threshold_100=config.get("threshold_100"),
                withdrew=False,
            )
    provider_result = dict(provider_result or {})
    if isinstance(provider_result.get("config"), dict):
        provider_result["config"] = dict(provider_result["config"])
    # Bullhorn stamps a local marker in its provider session. Discard it so
    # the following Application -> Organization -> Role phase owns all writes.
    db.rollback()

    final_app, _final_org, final_role = lock_auto_reject_context(
        db,
        organization_id=int(organization_id),
        application_id=application_id,
        require_live_role=False,
    )
    if final_app is None:
        raise _failure(
            "local_reconciliation_unavailable",
            "ATS reject succeeded but the local application is unavailable",
        )
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
    provider = (
        "bullhorn"
        if app.bullhorn_job_submission_id and not app.workable_candidate_id
        else "workable"
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
        receipt_key=str(payload.get("receipt_key") or "").strip() or None,
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
