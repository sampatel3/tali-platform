"""Durable producer/outbox dispatch for candidate assessment invites.

Queue acceptance is not delivery.  Producers only commit a delivery intent;
the Resend task later confirms a provider message id and atomically stamps the
invite/local pipeline.  A separate Workable outbox starts after that provider
confirmation, so ATS retry can never submit the candidate email again.
"""

from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session, joinedload, object_session

from ...models.assessment import Assessment
from ...models.organization import Organization
from ...platform.config import settings
from ...platform.request_context import get_request_id
from ...services.workable_actions_service import (
    resolve_workable_invite_stage,
    workable_writeback_enabled,
)

logger = logging.getLogger(__name__)

INVITE_PENDING_DISPATCH = "pending_dispatch"
INVITE_DISPATCHING = "dispatching"
INVITE_QUEUED = "queued"
INVITE_RETRYING = "retrying"
INVITE_RETRY_WAIT = "retry_wait"
INVITE_DISPATCH_FAILED = "dispatch_failed"

_SESSION_PAYLOADS_KEY = "assessment_invite_dispatch_payloads"
_SESSION_HOOK_KEY = "assessment_invite_dispatch_hook_installed"


def assessment_invite_idempotency_key(assessment: Assessment) -> str:
    """Stable Resend key for one logical invite delivery.

    Automatic retries retain the generation and therefore the exact same key.
    An explicit recruiter/agent *resend* increments the generation first so it
    remains a genuine new email instead of being collapsed into the original.
    """

    generation = max(
        0, int(getattr(assessment, "invite_email_send_generation", 0) or 0)
    )
    root = f"assessment-invite/{int(assessment.id)}"
    return root if generation == 0 else f"{root}/resend/{generation}"


def _workable_config(org: Organization) -> dict:
    config = org.workable_config if isinstance(org.workable_config, dict) else {}
    return {
        "workable_writeback": workable_writeback_enabled(org),
        "workflow_mode": str(config.get("workflow_mode") or "manual"),
        "invite_stage_name": str(config.get("invite_stage_name") or "").strip(),
    }


def _resolve_candidate_facing_brand(org: Organization) -> str | None:
    """Pull candidate_facing_brand from the org's workspace_settings JSON.

    Falls back to ``None`` when not set so the EmailService can use
    ``org_name`` as the next-best display name. Stripped + truncated to
    avoid weird inbox display.
    """
    settings_json = org.workspace_settings if isinstance(org.workspace_settings, dict) else {}
    raw = str(settings_json.get("candidate_facing_brand") or "").strip()
    return raw[:200] or None


def _send_taali_invite_email(
    *,
    candidate_email: str,
    candidate_name: str,
    token: str,
    assessment_id: int,
    org_name: str,
    position: str,
    candidate_facing_brand: str | None,
    reply_to: str | None,
    idempotency_key: str,
) -> None:
    # In production, accepting an assessment send without a configured
    # delivery provider would enqueue a task that merely logs "skipped" while
    # the candidate is moved to Invited. Refuse at the transaction boundary so
    # the assessment/decision stays retryable instead of becoming a false send.
    from ...platform.startup_validation import is_production_like

    resend_key = (settings.RESEND_API_KEY or "").strip().lower()
    if is_production_like(settings) and (
        resend_key in {"", "skip", "changeme"} or resend_key.startswith("your-")
    ):
        raise RuntimeError("RESEND_API_KEY is not configured for assessment delivery")
    from ...components.notifications.tasks import send_assessment_email

    send_assessment_email.delay(
        assessment_id=assessment_id,
        request_id=get_request_id(),
        reply_to=reply_to,
        idempotency_key=idempotency_key,
    )


def _workable_handoff_eligible(*, assessment: Assessment, org: Organization, config: dict) -> bool:
    """Should we ALSO update Workable in addition to sending the Taali email?"""
    if settings.MVP_DISABLE_WORKABLE:
        return False
    if not (
        org.workable_connected
        and org.workable_access_token
        and org.workable_subdomain
    ):
        return False
    if not assessment.workable_candidate_id:
        return False
    # Honor read-only mode — even if everything else is wired, write-back off
    # means "no Workable side effects on assessment send".
    if not config["workable_writeback"]:
        return False
    return True


def _bullhorn_handoff_eligible(*, assessment: Assessment, org: Organization) -> bool:
    """Whether a confirmed email should enter the Bullhorn handoff outbox.

    Stage-map availability is deliberately *not* checked here.  A missing
    ``invited`` mapping must become an explicit terminal needs-mapping surface
    after the email is confirmed, rather than silently suppressing the ATS
    handoff. Activation readiness catches the normal autonomous case earlier;
    this outbox rail also protects manual sends and mappings removed mid-run.
    """
    if not (
        getattr(org, "bullhorn_connected", False)
        and getattr(org, "bullhorn_client_id", None)
        and getattr(org, "bullhorn_refresh_token", None)
        and getattr(org, "bullhorn_username", None)
    ):
        return False
    app = getattr(assessment, "application", None)
    candidate = getattr(assessment, "candidate", None)
    return bool(
        app is not None
        and getattr(app, "bullhorn_job_submission_id", None)
        and candidate is not None
        and getattr(candidate, "bullhorn_candidate_id", None)
    )


def dispatch_assessment_invite_now(
    *,
    assessment: Assessment,
    org: Organization,
    candidate_email: str,
    candidate_name: str,
    position: str,
    reply_to: str | None = None,
) -> str:
    """Queue the provider task for one already-committed delivery intent.

    This function intentionally does not stamp ``invite_sent_at``, transition
    the application, or touch Workable.  Even a successful broker call proves
    only that a worker may run later.

    ``reply_to``: candidate replies route here (typically the recruiter's
    email). When None, replies hit the platform's no-reply address — fine
    for fully autonomous agent sends, less ideal for recruiter-triggered
    sends where the recruiter wants to handle responses themselves.
    """
    candidate_facing_brand = _resolve_candidate_facing_brand(org)
    _send_taali_invite_email(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        token=assessment.token,
        assessment_id=assessment.id,
        org_name=org.name if org else "Your recruiter",
        position=position,
        candidate_facing_brand=candidate_facing_brand,
        reply_to=reply_to,
        idempotency_key=assessment_invite_idempotency_key(assessment),
    )
    return INVITE_QUEUED


def _install_after_commit_dispatch(session: Session) -> None:
    """Install one outer-transaction dispatch hook on this request session."""
    if session.info.get(_SESSION_HOOK_KEY):
        return
    session.info[_SESSION_HOOK_KEY] = True

    @event.listens_for(session, "after_commit")
    def _dispatch_after_outer_commit(committed_session: Session) -> None:
        # SQLAlchemy emits after_commit for a released SAVEPOINT too.  External
        # delivery must wait for the root transaction that made the assessment
        # visible to other connections.
        if committed_session.in_nested_transaction():
            return
        payloads = committed_session.info.pop(_SESSION_PAYLOADS_KEY, {})
        for assessment_id, payload in list(payloads.items()):
            try:
                from ...components.notifications.tasks import (
                    dispatch_pending_assessment_invite,
                )

                dispatch_pending_assessment_invite.delay(
                    int(assessment_id),
                    reply_to=payload.get("reply_to"),
                )
            except Exception:
                # The committed Assessment row remains pending_dispatch. Beat's
                # durable sweep will retry, so a broker outage cannot lose it.
                logger.exception(
                    "assessment invite post-commit kick failed assessment_id=%s",
                    assessment_id,
                )

    @event.listens_for(session, "after_soft_rollback")
    def _discard_rolled_back_payloads(
        rolled_back_session: Session, previous_transaction
    ) -> None:
        payloads = rolled_back_session.info.get(_SESSION_PAYLOADS_KEY, {})
        if not payloads:
            return
        # A root rollback invalidates every pending payload.  A SAVEPOINT
        # rollback removes only intents registered inside that savepoint; other
        # work in the request can still commit safely.
        if getattr(previous_transaction, "parent", None) is None:
            rolled_back_session.info.pop(_SESSION_PAYLOADS_KEY, None)
            return
        for assessment_id, payload in list(payloads.items()):
            if payload.get("transaction") is previous_transaction:
                payloads.pop(assessment_id, None)


def dispatch_assessment_invite(
    *,
    assessment: Assessment,
    org: Organization,
    candidate_email: str,
    candidate_name: str,
    position: str,
    reply_to: str | None = None,
    pipeline_source: str = "agent",
    pipeline_actor_type: str = "system",
    pipeline_actor_id: int | None = None,
    pipeline_reason: str | None = None,
    pipeline_metadata: dict | None = None,
    pipeline_event_type: str = "assessment_invite_sent",
) -> str:
    """Durably request invite delivery in the producer's transaction.

    No email, broker call or Workable write occurs here.  The Assessment row is
    the outbox: ``pending_dispatch`` commits atomically with the invite/pipeline
    mutations, then an *outer* commit hook kicks a worker.  Beat sweeps any row
    whose kick was lost.  ``candidate_*``/``position`` remain in the signature
    for caller compatibility and early validation; the worker re-reads their
    canonical persisted values.
    """
    if not (candidate_email or "").strip():
        raise ValueError("candidate email is required for assessment delivery")
    if org is None:
        raise ValueError("organization is required for assessment delivery")
    session = object_session(assessment)
    if session is None:
        raise RuntimeError("assessment must be attached to a transaction")

    assessment.invite_email_status = INVITE_PENDING_DISPATCH
    # This is a new logical delivery intent (initial send or explicit resend).
    # Detach the previous provider id before committing so a late webhook for
    # the prior generation cannot overwrite the new pending outbox state.
    assessment.invite_email_id = None
    assessment.invite_delivered_at = None
    assessment.invite_opened_at = None
    assessment.invite_bounced_at = None
    assessment.invite_email_retry_count = 0
    assessment.invite_email_next_attempt_at = None
    assessment.invite_email_claimed_at = None
    assessment.invite_email_last_error = None
    assessment.invite_email_reply_to = (reply_to or "").strip() or None
    app = assessment.application
    if app is None and assessment.application_id is not None:
        from ...models.candidate_application import CandidateApplication

        app = session.query(CandidateApplication).filter(
            CandidateApplication.id == int(assessment.application_id),
            CandidateApplication.organization_id == int(assessment.organization_id),
        ).one_or_none()
    config = _workable_config(org)
    if _workable_handoff_eligible(assessment=assessment, org=org, config=config):
        handoff_provider = "workable"
        handoff_stage, _handoff_error = resolve_workable_invite_stage(
            org, getattr(assessment, "role", None)
        )
    elif _bullhorn_handoff_eligible(assessment=assessment, org=org):
        handoff_provider = "bullhorn"
        # A Taali intent, not a Bullhorn free-text status. The provider reverse
        # maps it through AtsStageMap in the serialized handoff worker.
        handoff_stage = "invited"
    else:
        handoff_provider = None
        handoff_stage = None
    assessment.invite_pipeline_transition = {
        "source": str(pipeline_source or "agent"),
        "actor_type": str(pipeline_actor_type or "system"),
        "actor_id": int(pipeline_actor_id) if pipeline_actor_id is not None else None,
        "reason": str(
            pipeline_reason or "Assessment invite accepted by email provider"
        ),
        "metadata": dict(pipeline_metadata or {}),
        "event_type": str(pipeline_event_type or "assessment_invite_sent"),
        "expected_stage": getattr(app, "pipeline_stage", None),
        "expected_version": (
            int(app.version or 0) if app is not None else None
        ),
        "ats_handoff_provider": handoff_provider,
        "workable_handoff_stage": handoff_stage,
    }
    payloads = session.info.setdefault(_SESSION_PAYLOADS_KEY, {})
    owner_transaction = session.get_nested_transaction() or session.get_transaction()
    payloads[int(assessment.id)] = {
        "reply_to": (reply_to or "").strip() or None,
        "transaction": owner_transaction,
    }
    _install_after_commit_dispatch(session)
    return INVITE_PENDING_DISPATCH


def deliver_pending_assessment_invite(
    db: Session,
    *,
    assessment_id: int,
    reply_to: str | None = None,
) -> dict:
    """Claim and deliver one Assessment-backed invite outbox record.

    The claim is committed before external work so duplicate task deliveries
    collapse.  A broker/provider exception restores ``pending_dispatch`` for
    Celery retry + Beat recovery.  Email-provider delivery remains tracked by
    ``send_assessment_email`` as ``sent``/``failed`` and Resend webhooks.
    """
    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.task),
            joinedload(Assessment.organization),
        )
        .filter(Assessment.id == int(assessment_id))
        # joinedload emits nullable outer joins. PostgreSQL must be told to
        # lock only the Assessment outbox row, not the related payload rows.
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None:
        return {"status": "missing", "assessment_id": int(assessment_id)}
    if bool(getattr(assessment, "is_voided", False)):
        assessment.invite_email_status = INVITE_DISPATCH_FAILED
        db.commit()
        return {"status": "voided", "assessment_id": int(assessment_id)}
    if assessment.invite_email_status != INVITE_PENDING_DISPATCH:
        return {
            "status": "already_claimed",
            "assessment_id": int(assessment_id),
            "invite_email_status": assessment.invite_email_status,
        }

    candidate = assessment.candidate
    org = assessment.organization
    task = assessment.task
    if candidate is None or not (candidate.email or "").strip() or org is None:
        assessment.invite_email_status = INVITE_DISPATCH_FAILED
        db.commit()
        return {
            "status": "invalid",
            "assessment_id": int(assessment_id),
            "detail": "persisted candidate email or organization is missing",
        }

    assessment.invite_email_status = INVITE_DISPATCHING
    db.commit()

    try:
        dispatch_assessment_invite_now(
            assessment=assessment,
            org=org,
            candidate_email=candidate.email,
            candidate_name=candidate.full_name or candidate.email,
            position=(task.name if task is not None else "Technical assessment"),
            reply_to=(reply_to or assessment.invite_email_reply_to),
        )
        # Eager workers/webhooks may already have advanced the state to sent or
        # delivered while the broker call returned. Refresh just this column and
        # never downgrade such evidence to queued.
        db.expire(assessment, ["invite_email_status"])
        current_status = str(assessment.invite_email_status or "").strip()
        if current_status == INVITE_DISPATCHING:
            assessment.invite_email_status = INVITE_QUEUED
        db.commit()
        return {
            "status": "queued",
            "assessment_id": int(assessment_id),
        }
    except Exception:
        db.rollback()
        retry_row = (
            db.query(Assessment)
            .filter(Assessment.id == int(assessment_id))
            .one_or_none()
        )
        if retry_row is not None:
            retry_row.invite_email_status = INVITE_PENDING_DISPATCH
            db.commit()
        raise


__all__ = [
    "INVITE_DISPATCHING",
    "INVITE_DISPATCH_FAILED",
    "INVITE_PENDING_DISPATCH",
    "INVITE_QUEUED",
    "INVITE_RETRYING",
    "INVITE_RETRY_WAIT",
    "assessment_invite_idempotency_key",
    "deliver_pending_assessment_invite",
    "dispatch_assessment_invite",
    "dispatch_assessment_invite_now",
]
