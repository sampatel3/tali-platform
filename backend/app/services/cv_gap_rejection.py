"""Provider-first rejection for one application with an unusable CV."""

from __future__ import annotations

from typing import Any

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    transition_outcome,
)
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .bullhorn_auto_reject import (
    BULLHORN_REJECT_FAILED,
    BULLHORN_REJECT_SUCCEEDED,
    bullhorn_reject_outcome,
)
from .document_service import sanitize_text_for_storage
from .workable_actions_service import disqualify_candidate_in_workable


class CvGapProviderChanged(RuntimeError):
    """The application routed to a different ATS after locked preflight."""


def _provider_context(
    db,
    *,
    org: Organization | None,
    app: CandidateApplication,
) -> tuple[Any, dict[str, Any]]:
    from ..components.integrations.bullhorn.provider import BullhornProvider
    from ..components.integrations.resolver import resolve_application_ats_provider

    provider = resolve_application_ats_provider(org, db, app)
    if isinstance(provider, BullhornProvider):
        target = str(getattr(app, "bullhorn_job_submission_id", None) or "").strip()
        return provider, {
            "provider": "bullhorn",
            "provider_target_id": target,
            "write_required": bool(target),
        }
    target = str(getattr(app, "workable_candidate_id", None) or "").strip()
    writeable = bool(
        org
        and getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    )
    return provider, {
        "provider": "workable" if target and writeable else "local",
        "provider_target_id": target if target and writeable else "",
        "write_required": bool(target and writeable),
    }


def cv_gap_provider_snapshot(
    db,
    *,
    org: Organization | None,
    app: CandidateApplication,
) -> dict[str, Any]:
    """Return the exact provider/target inputs fenced before network I/O."""

    _, snapshot = _provider_context(db, org=org, app=app)
    return snapshot


def perform_cv_gap_provider_reject(
    db,
    *,
    org: Organization | None,
    app: CandidateApplication,
    role: Role | None,
    reason: str,
    expected_provider_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Perform only provider I/O; callers finalize local state separately."""

    provider, current = _provider_context(db, org=org, app=app)
    expected = {
        "provider": str(expected_provider_snapshot.get("provider") or "local"),
        "provider_target_id": str(
            expected_provider_snapshot.get("provider_target_id") or ""
        ),
        "write_required": bool(expected_provider_snapshot.get("write_required")),
    }
    if current != expected:
        raise CvGapProviderChanged("ATS provider or target changed before rejection")
    if not current["write_required"]:
        return {**current, "success": True, "code": "local_only"}
    if current["provider"] == "bullhorn":
        result = provider.reject_application(app=app, role=role, reason=reason)
    else:
        result = disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=role,
            reason=reason,
            withdrew=False,
        )
    return {**current, **dict(result or {})}


def finalize_cv_gap_provider_reject(
    db,
    *,
    app: CandidateApplication,
    actor_type: str,
    actor_id: int | None,
    reason: str,
    trigger: str,
    operation_id: str,
    provider_result: dict[str, Any],
) -> dict[str, Any]:
    """Persist an exact provider result and local outcome under fresh locks."""

    provider = str(provider_result.get("provider") or "local")
    write_required = bool(provider_result.get("write_required"))
    if not provider_result.get("success"):
        message = sanitize_text_for_storage(
            str(provider_result.get("message") or f"{provider.title()} reject failed")
        ) or f"{provider.title()} reject failed"
        event_type = (
            "bullhorn_writeback_failed"
            if provider == "bullhorn"
            else "workable_writeback_failed"
        )
        append_application_event(
            db,
            app=app,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=message,
            metadata={
                "code": provider_result.get("code"),
                "trigger": trigger,
                "operation_id": operation_id,
                "provider_target_id": provider_result.get("provider_target_id"),
            },
            idempotency_key=f"{operation_id}:provider-failed"[:200],
        )
        return {"performed": False, "reason": message, f"{provider}_written": False}

    ensure_pipeline_fields(app)
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        metadata={"trigger": trigger, "ats_provider": provider},
        idempotency_key=f"{operation_id}:outcome"[:200],
        operation_receipt_key=operation_id,
    )
    if write_required:
        append_application_event(
            db,
            app=app,
            event_type=(
                "bullhorn_rejected" if provider == "bullhorn" else "workable_disqualified"
            ),
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata={
                "code": provider_result.get("code"),
                "trigger": trigger,
                "operation_id": operation_id,
                "provider_target_id": provider_result.get("provider_target_id"),
            },
            idempotency_key=f"{operation_id}:provider-applied"[:200],
        )
    return {
        "performed": True,
        "reason": reason,
        f"{provider}_written": write_required,
    }


def reject_for_cv_gap(
    *,
    db,
    org: Organization | None,
    app: CandidateApplication,
    role: Role | None,
    actor_type: str,
    actor_id: int | None = None,
    reason: str = "No CV on file",
    trigger: str = "reject_cv_gap",
    disqualify_fn=disqualify_candidate_in_workable,
) -> dict[str, Any]:
    """Reject after provider confirmation, or leave the local row open."""

    bullhorn_outcome = bullhorn_reject_outcome(
        db,
        app=app,
        org=org,
        role=role,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        trigger=trigger,
    )
    if bullhorn_outcome == BULLHORN_REJECT_SUCCEEDED:
        ensure_pipeline_fields(app)
        transition_outcome(
            db,
            app=app,
            to_outcome="rejected",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        return {"performed": True, "reason": reason, "bullhorn_written": True}
    if bullhorn_outcome == BULLHORN_REJECT_FAILED:
        return {
            "performed": False,
            "reason": "Bullhorn did not accept the rejection",
            "bullhorn_written": False,
        }

    workable_linked = bool(getattr(app, "workable_candidate_id", None))
    org_writeable = bool(
        org
        and getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    )
    wrote_to_workable = workable_linked and org_writeable
    if wrote_to_workable:
        result = disqualify_fn(
            org=org,
            app=app,
            role=role,
            reason=reason,
            withdrew=False,
        )
        if not result.get("success"):
            msg = sanitize_text_for_storage(
                str(
                    result.get("message")
                    or "Failed to disqualify candidate in Workable"
                )
            ) or "Failed to disqualify candidate in Workable"
            append_application_event(
                db,
                app=app,
                event_type="workable_writeback_failed",
                actor_type=actor_type,
                actor_id=actor_id,
                reason=msg,
                metadata={
                    "action": result.get("action"),
                    "code": result.get("code"),
                    "trigger": trigger,
                    "workable_candidate_id": app.workable_candidate_id,
                },
            )
            return {
                "performed": False,
                "reason": msg,
                "workable_result": result,
            }

    ensure_pipeline_fields(app)
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
    )
    if wrote_to_workable:
        append_application_event(
            db,
            app=app,
            event_type="workable_disqualified",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata={
                "trigger": trigger,
                "workable_candidate_id": app.workable_candidate_id,
            },
        )
    return {
        "performed": True,
        "reason": reason,
        "workable_written": wrote_to_workable,
    }


__all__ = [
    "CvGapProviderChanged",
    "cv_gap_provider_snapshot",
    "finalize_cv_gap_provider_reject",
    "perform_cv_gap_provider_reject",
    "reject_for_cv_gap",
]
