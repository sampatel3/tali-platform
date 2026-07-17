"""Durable claim/finalization helpers for provider-backed auto rejection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    transition_outcome,
)
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..decision_policy.auto_reject import evaluate_auto_reject_decision
from .document_service import sanitize_text_for_storage
from .auto_reject_operation_receipt import complete_auto_reject_operation
from .pre_screening_service import mark_auto_reject_state


def prepare_deferred_auto_reject_writeback(
    db,
    *,
    app: CandidateApplication,
    decision: dict[str, Any],
    provider: str,
    provider_target_id: str,
    actor_type: str,
    actor_id: int | None,
    receipt_key: str | None,
) -> dict[str, Any]:
    """Persist an honest in-flight receipt before releasing row locks."""

    label = "Bullhorn" if provider == "bullhorn" else "Workable"
    reason = sanitize_text_for_storage(
        str(decision.get("reason") or "Below pre-screen threshold")
    )
    mark_auto_reject_state(
        app,
        state="provider_writeback_in_progress",
        reason=f"{reason}; {label} rejection is in progress.",
        triggered=False,
    )
    append_application_event(
        db,
        app=app,
        event_type="auto_reject_writeback_started",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        metadata={
            "ats_provider": provider,
            "provider_target_id": provider_target_id,
            "pre_screen_score": (decision.get("snapshot") or {}).get(
                "pre_screen_score"
            ),
            "threshold_100": (decision.get("config") or {}).get(
                "threshold_100"
            ),
        },
        idempotency_key=(f"{receipt_key}:started" if receipt_key else None),
    )
    return {
        **decision,
        "performed": False,
        "state": "provider_writeback_in_progress",
        "provider_writeback_required": True,
        "provider": provider,
        "provider_target_id": provider_target_id,
    }


def finalize_deferred_auto_reject_success(
    db,
    *,
    app: CandidateApplication,
    role: Role | None,
    decision: dict[str, Any],
    provider: str,
    provider_result: dict[str, Any],
    actor_type: str,
    actor_id: int | None = None,
    receipt_key: str | None = None,
) -> dict[str, Any]:
    """Idempotently reconcile a confirmed ATS reject into local state."""

    del role  # retained in the public seam for provider-parity extensions
    snapshot = decision.get("snapshot") if isinstance(decision.get("snapshot"), dict) else {}
    config = decision.get("config") if isinstance(decision.get("config"), dict) else {}
    reason = sanitize_text_for_storage(
        str(decision.get("reason") or "Auto-rejected from pre-screen")
    )
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=(
            "Auto-rejected from pre-screen (Bullhorn)"
            if provider == "bullhorn"
            else "Auto-rejected from Workable pre-screen"
        ),
        idempotency_key=(f"{receipt_key}:outcome" if receipt_key else None),
        operation_receipt_key=(
            str(decision.get("operation_id") or "").strip() or None
        ),
    )
    if provider == "bullhorn":
        remote_status = sanitize_text_for_storage(
            str((provider_result.get("config") or {}).get("remote_status") or "")
        ) or None
        if remote_status:
            app.bullhorn_status = remote_status
            app.external_stage_raw = remote_status
            app.external_stage_normalized = "rejected"
            app.bullhorn_status_local_write_at = datetime.now(timezone.utc)
        append_application_event(
            db,
            app=app,
            event_type="bullhorn_rejected",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata={
                "code": provider_result.get("code"),
                "bullhorn_status": remote_status,
                "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
                "trigger": "auto_reject_pre_screen",
            },
            idempotency_key=(f"{receipt_key}:provider_confirmed" if receipt_key else None),
        )
        provider_metadata = {
            "bullhorn_written": True,
            "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
        }
    else:
        append_application_event(
            db,
            app=app,
            event_type="workable_disqualified",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata={
                "pre_screen_score": snapshot.get("pre_screen_score"),
                "threshold_100": config.get("threshold_100"),
                "workable_candidate_id": app.workable_candidate_id,
                "workable_actor_member_id": config.get("workable_actor_member_id"),
                "workable_disqualify_reason_id": config.get(
                    "workable_disqualify_reason_id"
                ),
            },
            idempotency_key=(f"{receipt_key}:provider_confirmed" if receipt_key else None),
        )
        provider_metadata = {
            "workable_written": True,
            "workable_candidate_id": app.workable_candidate_id,
        }
    append_application_event(
        db,
        app=app,
        event_type="auto_rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        metadata={
            "pre_screen_score": snapshot.get("pre_screen_score"),
            "cv_fit_score": snapshot.get("cv_fit_score"),
            "requirements_fit_score": snapshot.get("requirements_fit_score"),
            "threshold_100": config.get("threshold_100"),
            **provider_metadata,
        },
        idempotency_key=(f"{receipt_key}:auto_rejected" if receipt_key else None),
    )
    mark_auto_reject_state(app, state="rejected", reason=reason, triggered=True)
    complete_auto_reject_operation(
        app,
        operation_id=str(decision.get("operation_id") or ""),
    )
    return {
        **decision,
        "performed": True,
        "state": "rejected",
        "provider": provider,
        "provider_result": provider_result,
    }


def surface_deferred_auto_reject_failure(
    db,
    *,
    app: CandidateApplication,
    org: Organization | None,
    role: Role | None,
    provider: str,
    error_code: str,
    error_message: str,
    actor_type: str,
    actor_id: int | None = None,
    receipt_key: str | None = None,
    provider_outcome_uncertain: bool = False,
) -> dict[str, Any]:
    """Make terminal provider failure visible without overstating ATS outcome."""

    decision = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    message = sanitize_text_for_storage(error_message) or "ATS reject write-back failed"
    append_application_event(
        db,
        app=app,
        event_type=(
            "bullhorn_writeback_failed"
            if provider == "bullhorn"
            else "workable_writeback_failed"
        ),
        actor_type=actor_type,
        actor_id=actor_id,
        reason=message,
        metadata={
            "action": "auto_reject",
            "code": error_code,
            "ats_provider": provider,
            "trigger": "auto_reject_pre_screen",
        },
        idempotency_key=(f"{receipt_key}:provider_failed" if receipt_key else None),
    )
    append_application_event(
        db,
        app=app,
        event_type="auto_reject_failed",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=message,
        metadata={
            "code": error_code,
            "ats_provider": provider,
            "pre_screen_score": (decision.get("snapshot") or {}).get(
                "pre_screen_score"
            ),
            "threshold_100": (decision.get("config") or {}).get("threshold_100"),
        },
        idempotency_key=(f"{receipt_key}:failed" if receipt_key else None),
    )
    if provider_outcome_uncertain:
        provider_label = "Bullhorn" if provider == "bullhorn" else "Workable"
        local_outcome = str(app.application_outcome or "open").strip().lower()
        reconciliation_message = sanitize_text_for_storage(
            f"{provider_label} rejection could not be confirmed after the "
            "provider call began. Taali preserved the local outcome "
            f"'{local_outcome}'. Check the candidate in both systems before "
            "retrying or taking another outcome action."
        )
        mark_auto_reject_state(
            app,
            state="manual_reconciliation_required",
            reason=reconciliation_message,
            triggered=False,
        )
        append_application_event(
            db,
            app=app,
            event_type="auto_reject_manual_reconciliation_required",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reconciliation_message,
            metadata={
                "operation_id": receipt_key,
                "ats_provider": provider,
                "provider_called": None,
                "provider_succeeded": None,
                "provider_outcome_uncertain": True,
                "local_outcome_preserved": local_outcome,
                "error_code": error_code,
            },
            idempotency_key=(
                f"{receipt_key}:failure_reconcile" if receipt_key else None
            ),
        )
        return {
            **decision,
            "performed": False,
            "provider_performed": None,
            "provider_outcome_uncertain": True,
            "state": "manual_reconciliation_required",
            "reason": reconciliation_message,
        }
    if decision.get("should_trigger") and role is not None:
        from .application_automation_service import _divert_pre_screen_reject_to_card

        return _divert_pre_screen_reject_to_card(
            db,
            app=app,
            role=role,
            decision=decision,
            carded_reason=(
                "Below pre-screen threshold; the automated "
                f"{provider.title()} rejection failed ({message}) — surfaced "
                "for Decision Hub review."
            ),
            fallback_state="failed",
            fallback_reason=message,
        )
    mark_auto_reject_state(app, state="failed", reason=message, triggered=False)
    return {
        **decision,
        "performed": False,
        "state": "failed",
        "reason": message,
    }


__all__ = [
    "finalize_deferred_auto_reject_success",
    "prepare_deferred_auto_reject_writeback",
    "surface_deferred_auto_reject_failure",
]
