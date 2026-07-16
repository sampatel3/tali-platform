"""Automatic resolution for deterministic public-apply knockouts.

Screening-question knockouts are recruiter-authored boolean/choice rules, not
model judgments.  A running role may therefore resolve them without routine
HITL when either deterministic reject toggle is explicitly enabled.  The live
Role row is locked before the side effect so Turn off/Pause wins races, and an
ATS-linked application is rejected upstream before Taali closes it locally.

Callers retain the existing Decision Hub fallback whenever this function
returns ``False`` (policy off, role ineligible, or ATS write-back failure).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.agent_decision import AgentDecision
from ...models.organization import Organization
from ...models.role import Role
from ...services.job_page_lifecycle import role_accepts_native_applications
from ...services.pre_screening_service import mark_auto_reject_state
from ...services.role_execution_guard import (
    automatic_role_action_block_reason,
    lock_live_role,
)
from ..assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    transition_outcome,
)


logger = logging.getLogger("taali.job_pages.knockout_automation")


def _auto_reject_enabled(role: Role) -> bool:
    return bool(getattr(role, "auto_reject", False)) or bool(
        getattr(role, "auto_reject_pre_screen", False)
    )


def _live_eligible_role(db: Session, role: Role) -> Role | None:
    """Reload policy under a row lock so a concurrent Turn off/Pause wins."""
    live_role = lock_live_role(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
    if automatic_role_action_block_reason(live_role, db=db) is not None:
        return None
    if not role_accepts_native_applications(live_role, db=db):
        return None
    if not _auto_reject_enabled(live_role):
        return None
    return live_role


def _provider_application_id(provider_name: str, app: CandidateApplication) -> str:
    if provider_name == "workable":
        return str(getattr(app, "workable_candidate_id", None) or "").strip()
    if provider_name == "bullhorn":
        return str(getattr(app, "bullhorn_job_submission_id", None) or "").strip()
    return ""


def _write_provider_reject(
    db: Session,
    *,
    provider: Any,
    provider_name: str,
    app: CandidateApplication,
    role: Role,
    reason: str,
    failed_question_ids: list[int],
) -> bool:
    """Reject upstream first. Failure is audited and held for HITL."""
    try:
        result = provider.reject_application(app=app, role=role, reason=reason)
    except Exception as exc:  # provider failure must not fail public apply
        logger.exception(
            "%s knockout reject raised for application_id=%s",
            provider_name,
            app.id,
        )
        append_application_event(
            db,
            app=app,
            event_type=f"{provider_name}_writeback_failed",
            actor_type="system",
            reason=f"{provider_name.title()} knockout reject raised unexpectedly",
            metadata={
                "action": "disqualify",
                "source": "knockout_screening",
                "error_type": type(exc).__name__,
                "failed_question_ids": list(failed_question_ids),
            },
        )
        return False

    if not isinstance(result, dict) or not bool(result.get("success")):
        result = result if isinstance(result, dict) else {}
        append_application_event(
            db,
            app=app,
            event_type=f"{provider_name}_writeback_failed",
            actor_type="system",
            reason=str(result.get("message") or "ATS knockout reject failed"),
            metadata={
                "action": result.get("action") or "disqualify",
                "code": result.get("code"),
                "source": "knockout_screening",
                "failed_question_ids": list(failed_question_ids),
            },
        )
        return False

    append_application_event(
        db,
        app=app,
        event_type=f"{provider_name}_disqualified"
        if provider_name == "workable"
        else f"{provider_name}_rejected",
        actor_type="system",
        reason=reason,
        metadata={
            "action": result.get("action") or "disqualify",
            "code": result.get("code"),
            "source": "knockout_screening",
            "failed_question_ids": list(failed_question_ids),
        },
    )
    return True


def _discard_pending_knockout_cards(
    db: Session, *, application: CandidateApplication
) -> int:
    """Remove stale HITL cards after policy now permits deterministic action."""
    cards = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(application.id),
            AgentDecision.status == "pending",
            AgentDecision.decision_type == "skip_assessment_reject",
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    discarded = 0
    for card in cards:
        evidence = card.evidence if isinstance(card.evidence, dict) else {}
        if evidence.get("source") != "knockout_screening":
            continue
        card.status = "discarded"
        card.resolved_at = now
        card.resolution_note = (
            "Superseded by opted-in deterministic knockout auto-reject"
        )
        discarded += 1
    return discarded


def try_auto_resolve_knockout(
    db: Session,
    *,
    role: Role,
    application: CandidateApplication,
    reason: str,
    failed_question_ids: list[int],
) -> bool:
    """Resolve an opted-in deterministic knockout, returning success.

    Native applications have no upstream application id and are closed locally.
    A restored/imported application can retain an ATS id; in that case the ATS
    write must succeed before the local outcome changes.  Any hold/failure
    returns ``False`` so the caller emits the normal Decision Hub card.
    """
    live_role = _live_eligible_role(db, role)
    if live_role is None:
        return False

    org = (
        db.query(Organization)
        .filter(Organization.id == int(live_role.organization_id))
        .one_or_none()
    )
    from ...components.integrations.resolver import resolve_application_ats_provider

    provider = resolve_application_ats_provider(org, db, application)
    provider_name = str(getattr(provider, "ats", "") or "").strip().lower()
    provider_application_id = _provider_application_id(provider_name, application)
    ats_written = False

    # A restored row can retain an upstream identity. Never close it only in
    # Taali when that provider is now unavailable or a different connector won
    # resolution; hold it for HITL instead of creating split-brain ATS state.
    has_external_application = bool(
        str(getattr(application, "workable_candidate_id", None) or "").strip()
        or str(
            getattr(application, "bullhorn_job_submission_id", None) or ""
        ).strip()
    )
    if has_external_application and not provider_application_id:
        append_application_event(
            db,
            app=application,
            event_type="ats_writeback_unavailable",
            actor_type="system",
            reason="ATS-linked knockout reject held for recruiter review",
            metadata={
                "action": "disqualify",
                "source": "knockout_screening",
                "resolved_provider": provider_name or None,
                "failed_question_ids": list(failed_question_ids),
            },
        )
        return False

    if provider is not None and provider_application_id:
        ats_written = _write_provider_reject(
            db,
            provider=provider,
            provider_name=provider_name,
            app=application,
            role=live_role,
            reason=reason,
            failed_question_ids=failed_question_ids,
        )
        if not ats_written:
            return False

    ensure_pipeline_fields(application)
    # Preserve the specific, auditable reason on older knockout review cards
    # before the canonical close hook discards every other pending decision.
    # This remains in the same transaction as the outcome change, so a later
    # failure cannot leave the cards resolved while the application is open.
    if _discard_pending_knockout_cards(db, application=application):
        # Tests and some batch callers intentionally disable autoflush. Make
        # the specific resolution visible before the generic close query so
        # it cannot select and overwrite the same pending rows.
        db.flush()
    transition_outcome(
        db,
        app=application,
        to_outcome="rejected",
        actor_type="system",
        reason="Auto-rejected by deterministic application screening",
        metadata={
            "source": "knockout_screening",
            "failed_question_ids": list(failed_question_ids),
        },
    )
    append_application_event(
        db,
        app=application,
        event_type="auto_rejected",
        actor_type="system",
        reason=reason,
        metadata={
            "source": "knockout_screening",
            "failed_question_ids": list(failed_question_ids),
            "ats_provider": provider_name or "standalone",
            "ats_written": ats_written,
        },
    )
    mark_auto_reject_state(
        application,
        state="rejected",
        reason=reason,
        triggered=True,
    )
    return True


__all__ = ["try_auto_resolve_knockout"]
