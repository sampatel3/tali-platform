"""Best-effort Workable side effects for recruiter-resolved decisions.

Two responsibilities, both invoked from ``approve_decision`` /
``override_decision`` after the underlying action has already succeeded:

1. ``try_workable_advance`` — move the candidate in Workable to the org's
   configured ``advance_stage_name`` whenever the recruiter advanced (or
   skip-advanced) them. Without this, "Advance" / "Skip & advance" only
   change Tali's internal pipeline_stage and the recruiter's Workable
   view is silently out of sync.

2. ``post_decision_summary_to_workable`` — post a short activity-feed note
   ("TAALI ▸ Advanced by recruiter · score 85 · …  Report (30d): https://…")
   on every recruiter-resolved decision so the Workable side has a
   one-glance audit trail with a 30-day share link to the full Tali
   report.

Both are best-effort: failures are recorded as application events and
returned as booleans / no-ops, never raised. The caller has already
committed the actual stage / outcome change before this fires.

Recruiter-only. ``actor.user_id`` is required so we can attribute the
minted ``ShareLink`` row to the same recruiter who clicked the button.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import append_application_event
from ..domains.integrations_notifications.adapters import build_workable_adapter
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..models.share_link import SHARE_LINK_MODE_RECRUITER, ShareLink
from ..platform.config import settings
from .types import Actor

logger = logging.getLogger("taali.actions.workable_decision_summary")

_SHARE_LINK_TTL = timedelta(days=30)
_NOTE_BODY_CAP = 1200  # Workable accepts more, but keep the activity feed legible.


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _workable_writeback_ready(
    *,
    app: CandidateApplication,
    org: Optional[Organization],
) -> bool:
    if settings.MVP_DISABLE_WORKABLE:
        return False
    if not getattr(app, "workable_candidate_id", None):
        return False
    if org is None:
        return False
    return bool(
        getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    )


def try_workable_advance(
    db: Session,
    actor: Actor,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    role: Optional[Role],
    target_stage: Optional[str],
    reason: Optional[str] = None,
) -> bool:
    """Move the candidate in Workable to ``target_stage``.

    ``target_stage`` is the recruiter's pick (sent in the approve / override
    request body from the Workable stage `<select>` rendered in the home-
    page modal). When unset / empty, the Workable move is skipped; only
    Tali's internal pipeline_stage changes. Returns True iff the move
    succeeded. Failures record a ``workable_writeback_failed`` event and
    return False — the underlying stage change has already committed.
    """
    target = (target_stage or "").strip()
    if not target:
        return False
    if not _workable_writeback_ready(app=app, org=org):
        return False
    assert org is not None  # narrowed by _workable_writeback_ready

    from ..services.workable_actions_service import move_candidate_in_workable

    try:
        result = move_candidate_in_workable(
            org=org,
            candidate_id=str(app.workable_candidate_id),
            target_stage=target,
            role=role,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "workable advance raised unexpectedly (application_id=%s)", app.id
        )
        return False

    config = result.get("config") or {}
    if not result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_failed",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason=result.get("message") or "Workable move failed",
            metadata={
                "action": result.get("action"),
                "code": result.get("code"),
                "workable_candidate_id": app.workable_candidate_id,
                "target_stage": target,
                "source": "decision_summary",
            },
        )
        logger.warning(
            "workable advance failed application_id=%s code=%s message=%s",
            app.id,
            result.get("code"),
            result.get("message"),
        )
        return False

    app.workable_stage = target
    append_application_event(
        db,
        app=app,
        event_type="workable_moved",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=reason or "Advanced by recruiter (decision resolution)",
        metadata={
            "target_stage": target,
            "workable_candidate_id": app.workable_candidate_id,
            "workable_actor_member_id": config.get("actor_member_id"),
            "source": "decision_summary",
        },
    )
    return True


def _mint_30d_share_link(
    db: Session,
    *,
    app: CandidateApplication,
    created_by_user_id: Optional[int],
) -> Optional[str]:
    """Create a 30-day recruiter-mode share link, return the public URL.

    Returns None when the row insert fails — the caller falls back to a
    note without a link rather than failing the whole summary.
    """
    try:
        link = ShareLink(
            organization_id=app.organization_id,
            application_id=app.id,
            created_by_user_id=created_by_user_id,
            token=f"shr_{secrets.token_urlsafe(24)}",
            mode=SHARE_LINK_MODE_RECRUITER,
            expiry_preset="30d",
            expires_at=_utcnow() + _SHARE_LINK_TTL,
        )
        db.add(link)
        db.flush()
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "share-link mint failed for application_id=%s", app.id
        )
        return None

    frontend = (settings.FRONTEND_URL or "").rstrip("/")
    if not frontend:
        return None
    return f"{frontend}/share/{link.token}"


_VERDICT_HEADLINES = {
    "advanced": "Advanced by recruiter",
    "skip_advanced": "Skipped assessment and advanced by recruiter",
    "rejected": "Rejected by recruiter",
    "assessment_sent": "Assessment sent by recruiter",
    "invite_resent": "Assessment invite resent by recruiter",
}


def _format_score(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.0f}/100"
    except Exception:
        return None


def _format_confidence(value) -> Optional[str]:
    if value is None:
        return None
    try:
        pct = float(value) * 100
    except Exception:
        return None
    return f"{pct:.0f}%"


def _truncate(text: str, *, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def compose_decision_summary_note(
    decision: AgentDecision,
    app: CandidateApplication,
    *,
    verdict: str,
    override_action: Optional[str] = None,
    reason: Optional[str] = None,
    share_url: Optional[str] = None,
) -> str:
    """Build the short Workable activity-feed body.

    Layout (kept ≤6 lines so it stays scannable in Workable's feed):

        TAALI ▸ {headline}
        Score: 85/100 · Tali confidence: 80%
        Agent recommended: {decision_type} — "{reasoning}"
        Recruiter note: {note}
        Report (30 days): https://taali.ai/share/shr_…
    """
    lines: list[str] = []
    headline = _VERDICT_HEADLINES.get(verdict, verdict.replace("_", " ").title())
    if override_action and verdict not in {"skip_advanced"}:
        lines.append(f"TAALI ▸ {headline} (override → {override_action})")
    else:
        lines.append(f"TAALI ▸ {headline}")

    score_bits: list[str] = []
    score = _format_score(getattr(app, "pre_screen_score_100", None))
    if score:
        score_bits.append(f"Score: {score}")
    confidence = _format_confidence(getattr(decision, "confidence", None))
    if confidence:
        score_bits.append(f"Tali confidence: {confidence}")
    if score_bits:
        lines.append(" · ".join(score_bits))

    agent_reasoning = _truncate(decision.reasoning or "", limit=240)
    if agent_reasoning:
        lines.append(
            f"Agent recommended: {decision.decision_type} — \"{agent_reasoning}\""
        )

    recruiter_note = _truncate(reason or "", limit=200)
    if recruiter_note:
        lines.append(f"Recruiter note: {recruiter_note}")

    if share_url:
        lines.append(f"Report (30 days): {share_url}")

    body = "\n".join(lines).strip()
    if len(body) > _NOTE_BODY_CAP:
        body = body[: _NOTE_BODY_CAP - 1].rstrip() + "…"
    return body


def post_decision_summary_to_workable(
    db: Session,
    actor: Actor,
    *,
    app: CandidateApplication,
    org: Optional[Organization],
    decision: AgentDecision,
    verdict: str,
    override_action: Optional[str] = None,
    reason: Optional[str] = None,
) -> bool:
    """Post the short decision-resolution note to Workable.

    Returns True iff the note was posted. Skips silently (False) when
    Workable isn't connected or the application isn't linked; logs +
    records a failure event when the API call itself errors.
    """
    if not _workable_writeback_ready(app=app, org=org):
        return False
    assert org is not None  # narrowed above

    share_url = _mint_30d_share_link(
        db, app=app, created_by_user_id=actor.user_id
    )
    body = compose_decision_summary_note(
        decision,
        app,
        verdict=verdict,
        override_action=override_action,
        reason=reason,
        share_url=share_url,
    )

    adapter = build_workable_adapter(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    try:
        result = adapter.post_candidate_activity(
            candidate_id=str(app.workable_candidate_id), body=body
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "workable decision-summary note raised (application_id=%s)", app.id
        )
        return False

    if not result.get("success"):
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_failed",
            actor_type=actor.type,
            actor_id=actor.event_actor_id,
            reason="decision-summary note post failed",
            metadata={
                "decision_id": int(decision.id),
                "verdict": verdict,
                "override_action": override_action,
                "share_url": share_url,
                "error": str(result.get("error") or ""),
                "source": "decision_summary",
            },
        )
        logger.warning(
            "workable decision-summary post failed application_id=%s decision_id=%s err=%s",
            app.id,
            decision.id,
            result.get("error"),
        )
        return False

    append_application_event(
        db,
        app=app,
        event_type="workable_decision_note_posted",
        actor_type=actor.type,
        actor_id=actor.event_actor_id,
        reason=f"Decision resolution note posted to Workable ({verdict})",
        metadata={
            "decision_id": int(decision.id),
            "verdict": verdict,
            "override_action": override_action,
            "share_url": share_url,
            "body_preview": body[:240],
        },
    )
    return True
