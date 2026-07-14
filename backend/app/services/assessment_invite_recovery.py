"""Deterministic recovery for expired and bounced assessment invites.

The assessment row remains the delivery outbox.  This service only decides
whether a *new logical invite generation* may be requested automatically or
whether the incident must surface as one governed ``AgentDecision``.  It never
sends mail directly.

Recovery is deliberately conservative:

* ``role.auto_resend_assessment`` (including the legacy ``auto_promote``
  fallback) must authorize an automatic resend.
* hard bounce / complaint and any email suppression always require HITL;
* at most one expiry recovery is auto-resent per Assessment lifetime;
* each provider-message / expiry-window incident is idempotent;
* the normal resend action re-checks the live role, current task and delivery
  suppression before it creates the durable outbox intent.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, joinedload

from ..actions import queue_decision, resend_assessment_invite
from ..actions.types import Actor
from ..components.assessments.repository import append_assessment_timeline_event
from ..domains.assessments_runtime.role_support import is_resolved
from ..models.agent_decision import AgentDecision
from ..models.agent_run import AgentRun
from ..models.assessment import Assessment, AssessmentStatus
from .agent_policy_settings import automation_enabled_for_decision
from .email_suppression_service import is_suppressed


logger = logging.getLogger(__name__)

RECOVERY_TRIGGER_EXPIRED = "expired"
RECOVERY_TRIGGER_BOUNCE = "bounce"
_RECOVERY_TRIGGERS = frozenset({RECOVERY_TRIGGER_EXPIRED, RECOVERY_TRIGGER_BOUNCE})

_AUTO_EVENT = "assessment_invite_recovery_auto_resent"
_HITL_EVENT = "assessment_invite_recovery_hitl_queued"
_SUPPRESSED_EVENT = "assessment_invite_recovery_human_suppressed"
_MAX_AUTO_RECOVERY_RESENDS = 1
_MODEL_VERSION = "deterministic-invite-recovery-v1"
_PROMPT_VERSION = "assessment-invite-recovery-v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _expiry_is_due(assessment: Assessment, now: datetime) -> bool:
    expires_at = _aware(assessment.expires_at)
    return (
        assessment.status == AssessmentStatus.EXPIRED
        or (expires_at is not None and expires_at <= now)
    )


def _incident_key(
    assessment: Assessment,
    *,
    trigger: str,
    provider_email_id: str | None,
    provider_status: str | None,
) -> str:
    generation = int(assessment.invite_email_send_generation or 0)
    if trigger == RECOVERY_TRIGGER_BOUNCE:
        subject = (
            str(provider_email_id or assessment.invite_email_id or "unknown").strip()
            or "unknown"
        )
        status = str(provider_status or assessment.invite_email_status or "bounced")
        return f"assessment:{int(assessment.id)}:delivery:{status}:{subject}"
    expires_at = _aware(assessment.expires_at)
    window = expires_at.isoformat() if expires_at is not None else "unknown"
    return f"assessment:{int(assessment.id)}:expiry:{generation}:{window}"


def _timeline_incident_status(
    assessment: Assessment, incident_key: str
) -> str | None:
    for event in reversed(list(assessment.timeline or [])):
        if not isinstance(event, dict) or event.get("incident_key") != incident_key:
            continue
        event_type = str(event.get("event_type") or "")
        if event_type == _AUTO_EVENT:
            return "auto_resent"
        if event_type == _HITL_EVENT:
            return "awaiting_recruiter_approval"
        if event_type == _SUPPRESSED_EVENT:
            return "human_suppressed"
    return None


def _evidence_has_incident(evidence: dict[str, Any], incident_key: str) -> bool:
    if evidence.get("recovery_incident_key") == incident_key:
        return True
    keys = evidence.get("recovery_incident_keys")
    return isinstance(keys, list) and incident_key in keys


def _decision_for_incident(
    db: Session,
    *,
    assessment: Assessment,
    incident_key: str,
) -> AgentDecision | None:
    if assessment.application_id is None or assessment.role_id is None:
        return None
    rows = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == int(assessment.organization_id),
            AgentDecision.role_id == int(assessment.role_id),
            AgentDecision.application_id == int(assessment.application_id),
            AgentDecision.decision_type == "resend_assessment_invite",
        )
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
        .limit(100)
        .all()
    )
    return next(
        (
            row
            for row in rows
            if _evidence_has_incident(dict(row.evidence or {}), incident_key)
        ),
        None,
    )


def _pending_decision(db: Session, assessment: Assessment) -> AgentDecision | None:
    if assessment.application_id is None:
        return None
    return (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(assessment.application_id),
            AgentDecision.status.in_(("pending", "processing")),
        )
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
        .first()
    )


def _auto_recovery_count(assessment: Assessment) -> int:
    return sum(
        1
        for event in list(assessment.timeline or [])
        if isinstance(event, dict) and event.get("event_type") == _AUTO_EVENT
    )


def _action_allowlist_allows(role: Any) -> bool:
    configured = getattr(role, "agent_action_allowlist", None)
    if configured is None:
        return True
    if not isinstance(configured, (list, tuple, set, frozenset)):
        return False
    return "resend_assessment_invite" in {
        str(value).strip() for value in configured if str(value).strip()
    }


def _new_agent_run(
    db: Session,
    *,
    assessment: Assessment,
    trigger: str,
    incident_key: str,
) -> AgentRun:
    run = AgentRun(
        organization_id=int(assessment.organization_id),
        role_id=int(assessment.role_id),
        trigger="event",
        status="running",
        started_at=_utcnow(),
        model_version=_MODEL_VERSION,
        prompt_version=_PROMPT_VERSION,
        agent_state_snapshot={
            "assessment_invite_recovery": {
                "assessment_id": int(assessment.id),
                "trigger": trigger,
                "incident_key": incident_key,
            }
        },
    )
    db.add(run)
    db.flush()
    return run


def _finish_run(run: AgentRun, *, decision_created: bool) -> None:
    run.status = "succeeded"
    run.finished_at = _utcnow()
    run.decisions_emitted = 1 if decision_created else 0
    run.tools_called = [{"name": "resend_assessment_invite", "count": 1}]


def _reasoning(trigger: str, hold_reason: str | None) -> str:
    if trigger == RECOVERY_TRIGGER_BOUNCE:
        base = (
            "Resend reported a hard delivery failure for this assessment invite. "
            "Verify or correct the candidate email before approving another invite."
        )
    else:
        base = (
            "The assessment invite expired before the candidate started. "
            "A fresh invite window is ready to be issued."
        )
    if hold_reason:
        return f"{base} Automatic resend was held: {hold_reason}."
    return base


def _queue_hitl(
    db: Session,
    *,
    assessment: Assessment,
    run: AgentRun,
    trigger: str,
    incident_key: str,
    provider_email_id: str | None,
    provider_status: str | None,
    suppression_reason: str | None,
    auto_authorized: bool,
    hold_reason: str | None,
) -> dict[str, Any]:
    evidence = {
        "assessment_id": int(assessment.id),
        "recovery_trigger": trigger,
        "recovery_incident_key": incident_key,
        "provider_email_id": provider_email_id,
        "provider_status": provider_status or assessment.invite_email_status,
        "invite_email_send_generation": int(
            assessment.invite_email_send_generation or 0
        ),
        "suppression_reason": suppression_reason,
        "auto_resend_authorized": bool(auto_authorized),
        "auto_resend_hold_reason": hold_reason,
        "requires_candidate_email_review": bool(
            trigger == RECOVERY_TRIGGER_BOUNCE or suppression_reason
        ),
    }
    scope = hashlib.sha256(incident_key.encode("utf-8")).hexdigest()[:20]
    decision = queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(assessment.organization_id),
        role_id=int(assessment.role_id),
        application_id=int(assessment.application_id),
        decision_type="resend_assessment_invite",
        reasoning=_reasoning(trigger, hold_reason),
        evidence=evidence,
        confidence=1.0,
        model_version=_MODEL_VERSION,
        prompt_version=_PROMPT_VERSION,
        recommendation="resend_assessment_invite",
        idempotency_key_suffix=f"invite-recovery:{scope}",
        deduplication_scope=incident_key,
    )
    created = bool(getattr(decision, "_just_created", False))
    _finish_run(run, decision_created=created)

    # Another agent can queue a candidate decision without locking this
    # Assessment.  ``queue_decision`` correctly returns that existing row;
    # never mislabel it as the invite-recovery card.  The periodic sweep will
    # retry after the unrelated decision is resolved.
    returned_evidence = dict(decision.evidence or {})
    if (
        decision.decision_type != "resend_assessment_invite"
        or int(returned_evidence.get("assessment_id") or 0)
        != int(assessment.id)
    ):
        db.commit()
        return {
            "status": "deferred_existing_decision",
            "assessment_id": int(assessment.id),
            "decision_id": int(decision.id),
            "incident_key": incident_key,
        }

    # A prior explicit human no is authoritative.  Mark this exact delivery
    # incident handled without mutating the immutable evidence of that older
    # decision, so the periodic sweep cannot create empty AgentRuns forever.
    if decision.status in {"discarded", "overridden"}:
        append_assessment_timeline_event(
            assessment,
            _SUPPRESSED_EVENT,
            {"incident_key": incident_key, "decision_id": int(decision.id)},
        )
        db.commit()
        return {
            "status": "human_suppressed",
            "assessment_id": int(assessment.id),
            "decision_id": int(decision.id),
            "incident_key": incident_key,
        }

    append_assessment_timeline_event(
        assessment,
        _HITL_EVENT,
        {
            "incident_key": incident_key,
            "decision_id": int(decision.id),
            "trigger": trigger,
        },
    )
    db.commit()
    return {
        "status": "awaiting_recruiter_approval",
        "assessment_id": int(assessment.id),
        "decision_id": int(decision.id),
        "incident_key": incident_key,
        "created": created,
    }


def recover_assessment_invite(
    db: Session,
    *,
    assessment_id: int,
    trigger: str,
    provider_email_id: str | None = None,
    provider_status: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recover one invite incident, idempotently.

    Automatic recovery creates a new invite generation through
    ``actions.resend_assessment_invite``.  Every held path creates (or reuses)
    one incident-scoped ``AgentDecision`` so routine sweeps and duplicate
    webhooks never pile up recruiter cards.
    """

    normalized_trigger = str(trigger or "").strip().lower()
    if normalized_trigger not in _RECOVERY_TRIGGERS:
        raise ValueError(f"unsupported assessment invite recovery trigger: {trigger!r}")
    current_time = now or _utcnow()

    assessment = (
        db.query(Assessment)
        .options(
            joinedload(Assessment.candidate),
            joinedload(Assessment.application),
            joinedload(Assessment.role),
            joinedload(Assessment.task),
        )
        .filter(Assessment.id == int(assessment_id))
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        return {"status": "missing", "assessment_id": int(assessment_id)}
    if bool(assessment.is_voided):
        return {"status": "skipped_voided", "assessment_id": int(assessment.id)}
    if assessment.status in {
        AssessmentStatus.IN_PROGRESS,
        AssessmentStatus.COMPLETED,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    }:
        return {"status": "skipped_terminal", "assessment_id": int(assessment.id)}
    if (
        normalized_trigger == RECOVERY_TRIGGER_EXPIRED
        and not _expiry_is_due(assessment, current_time)
    ):
        return {"status": "not_due", "assessment_id": int(assessment.id)}
    if normalized_trigger == RECOVERY_TRIGGER_BOUNCE and str(
        provider_status or assessment.invite_email_status or ""
    ).lower() not in {"bounced", "complained"}:
        return {"status": "not_due", "assessment_id": int(assessment.id)}

    role = assessment.role
    application = assessment.application
    if (
        role is None
        or assessment.role_id is None
        or application is None
        or assessment.application_id is None
    ):
        return {
            "status": "skipped_unanchored",
            "assessment_id": int(assessment.id),
        }
    # Recovery is part of an enabled role agent, not a hidden automation for a
    # role the recruiter has turned off.  Paused agents still surface a HITL
    # exception below, but a disabled role creates no new agent work.
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return {
            "status": "skipped_agent_disabled",
            "assessment_id": int(assessment.id),
        }
    if is_resolved(application):
        return {
            "status": "skipped_resolved_application",
            "assessment_id": int(assessment.id),
        }

    incident_key = _incident_key(
        assessment,
        trigger=normalized_trigger,
        provider_email_id=provider_email_id,
        provider_status=provider_status,
    )
    existing = _decision_for_incident(
        db, assessment=assessment, incident_key=incident_key
    )
    if existing is not None:
        return {
            "status": (
                "awaiting_recruiter_approval"
                if existing.status in {"pending", "processing"}
                else str(existing.status)
            ),
            "assessment_id": int(assessment.id),
            "decision_id": int(existing.id),
            "incident_key": incident_key,
            "deduplicated": True,
        }
    timeline_status = _timeline_incident_status(assessment, incident_key)
    if timeline_status is not None:
        return {
            "status": timeline_status,
            "assessment_id": int(assessment.id),
            "incident_key": incident_key,
            "deduplicated": True,
        }

    pending = _pending_decision(db, assessment)
    if pending is not None:
        pending_evidence = dict(pending.evidence or {})
        same_assessment_resend = (
            pending.decision_type == "resend_assessment_invite"
            and int(pending_evidence.get("assessment_id") or 0) == int(assessment.id)
        )
        if same_assessment_resend:
            keys = list(pending_evidence.get("recovery_incident_keys") or [])
            if incident_key not in keys:
                keys.append(incident_key)
                pending_evidence["recovery_incident_keys"] = keys
                pending.evidence = pending_evidence
                append_assessment_timeline_event(
                    assessment,
                    _HITL_EVENT,
                    {
                        "incident_key": incident_key,
                        "decision_id": int(pending.id),
                        "trigger": normalized_trigger,
                    },
                )
                db.commit()
            return {
                "status": "awaiting_recruiter_approval",
                "assessment_id": int(assessment.id),
                "decision_id": int(pending.id),
                "incident_key": incident_key,
                "deduplicated": True,
            }
        # The decision queue intentionally permits one pending decision per
        # candidate.  The periodic expiry/bounce sweep will retry after this
        # unrelated card is resolved; do not stack a second card now.
        return {
            "status": "deferred_existing_decision",
            "assessment_id": int(assessment.id),
            "decision_id": int(pending.id),
            "incident_key": incident_key,
        }

    candidate_email = (
        (assessment.candidate.email if assessment.candidate is not None else None)
        or ""
    ).strip()
    suppression_reason = (
        is_suppressed(
            db,
            email=candidate_email,
            organization_id=int(assessment.organization_id),
        )
        if candidate_email
        else "missing_candidate_email"
    )
    auto_authorized = automation_enabled_for_decision(
        role, "resend_assessment_invite"
    )
    hold_reason: str | None = None
    if normalized_trigger == RECOVERY_TRIGGER_BOUNCE:
        hold_reason = "hard bounce or complaint requires candidate email review"
    elif suppression_reason:
        hold_reason = f"candidate email is suppressed ({suppression_reason})"
    elif not auto_authorized:
        hold_reason = "role.auto_resend_assessment is not enabled"
    elif not _action_allowlist_allows(role):
        hold_reason = "resend_assessment_invite is not in the role action allowlist"
    elif _auto_recovery_count(assessment) >= _MAX_AUTO_RECOVERY_RESENDS:
        hold_reason = "automatic recovery resend limit reached (1 per assessment)"

    run = _new_agent_run(
        db,
        assessment=assessment,
        trigger=normalized_trigger,
        incident_key=incident_key,
    )
    if hold_reason is None:
        result = resend_assessment_invite.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(assessment.organization_id),
            assessment_id=int(assessment.id),
        )
        if result.status == "queued":
            append_assessment_timeline_event(
                assessment,
                _AUTO_EVENT,
                {
                    "incident_key": incident_key,
                    "trigger": normalized_trigger,
                    "agent_run_id": int(run.id),
                    "new_send_generation": int(
                        assessment.invite_email_send_generation or 0
                    ),
                },
            )
            _finish_run(run, decision_created=False)
            db.commit()
            return {
                "status": "auto_resent",
                "assessment_id": int(assessment.id),
                "agent_run_id": int(run.id),
                "incident_key": incident_key,
                "send_generation": int(
                    assessment.invite_email_send_generation or 0
                ),
            }
        hold_reason = result.detail or f"resend action returned {result.status}"

    return _queue_hitl(
        db,
        assessment=assessment,
        run=run,
        trigger=normalized_trigger,
        incident_key=incident_key,
        provider_email_id=provider_email_id,
        provider_status=provider_status,
        suppression_reason=suppression_reason,
        auto_authorized=auto_authorized,
        hold_reason=hold_reason,
    )


__all__ = [
    "RECOVERY_TRIGGER_BOUNCE",
    "RECOVERY_TRIGGER_EXPIRED",
    "recover_assessment_invite",
]
