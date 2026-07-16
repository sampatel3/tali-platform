"""Bullhorn siblings of the shared op_runner's ATS-write handlers.

``services/workable_op_runner`` owns the op dispatch, the shared retry / requeue /
surface machinery, and the Workable-shaped handler bodies. For a Bullhorn-connected
org those handlers early-delegate here (build plan §6 line 89 — "op_runner resolves
provider through the PR-1 seam") so the ATS write goes to :class:`BullhornProvider`
while everything cross-cutting stays in the ONE shared shell — **no new op types, no
new Celery task, no change to gated/ungated semantics or retry policy.**

Each handler is the Bullhorn analogue of one Workable handler and returns the same
shell-compatible ``{"status": ...}`` dict. Under ``strict_workable_writes()`` the
provider's write raises the shared :class:`WorkableWritebackError`, which the shell
already turns into a retry (retriable) or a terminal ``surface_op_failure`` — the
SAME terminal-failure surface Workable ops use — so a server-side workflow-validation
rejection on a status write lands in the Decision Hub exactly like a Workable one.

Gating: reached only when ``resolve_ats_provider(org, db)`` returns a
:class:`BullhornProvider`, i.e. ``BULLHORN_ENABLED`` on AND the org is
Bullhorn-connected. A no-op otherwise (the Workable handler body runs instead).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....services.ats_operation_guards import lock_live_application_move
from ....services.workable_actions_service import WorkableWritebackError
from .provider import BullhornProvider

logger = logging.getLogger("taali.bullhorn.op_handlers")


def _bullhorn_provider(
    db: Session, org: Organization, app: CandidateApplication
) -> BullhornProvider | None:
    """Resolve Bullhorn from this application's durable ATS linkage."""
    from ..resolver import resolve_application_ats_provider

    provider = resolve_application_ats_provider(org, db, app)
    return provider if isinstance(provider, BullhornProvider) else None


def _raise_if_failed(result: dict, *, default_action: str) -> None:
    """Turn a non-strict failure dict into a WorkableWritebackError.

    Under strict mode the provider already raised; this covers the ungated path
    (e.g. the free-form note op, which is not strict-gated) so a failed write is
    surfaced/retried by the shell instead of silently succeeding.
    """
    if result.get("success"):
        return
    code = str(result.get("code") or "api_error")
    raise WorkableWritebackError(
        action=str(result.get("action") or default_action),
        code=code,
        message=str(result.get("message") or "Bullhorn write failed"),
        retriable=(code == "api_error"),
    )


def run_move_stage(db: Session, org: Organization, app: CandidateApplication, payload: dict) -> dict:
    """Bullhorn hand-back move — analogue of ``_op_move_stage``.

    The provider reverse-maps the supplied Taali intent (``advanced`` for a
    recruiter hand-back, ``invited`` for the confirmed assessment handoff) to
    the org's status, never guesses, writes it, and stamps local-write. Gated:
    Tali's advance transition only follows a confirmed remote write (strict
    mode raises on failure).
    """
    from ....domains.assessments_runtime.pipeline_service import (
        append_application_event,
        transition_stage,
    )
    from ....services.workable_actions_service import strict_workable_writes

    application_id = int(app.id)
    app = lock_live_application_move(
        db,
        organization_id=int(org.id),
        application_id=application_id,
    )
    if not app.bullhorn_job_submission_id:
        raise WorkableWritebackError(
            action="move",
            code="not_linked",
            message="The application is no longer linked to Bullhorn",
            retriable=False,
        )
    provider = _bullhorn_provider(db, org, app)
    if provider is None:
        raise WorkableWritebackError(
            action="move",
            code="not_configured",
            message="Bullhorn is no longer connected for this application",
            retriable=False,
        )

    reason = payload.get("reason")
    user_id = payload.get("user_id")
    actor_type = str(payload.get("actor_type") or "recruiter")
    actor_id = payload.get("actor_id", user_id)
    source = str(payload.get("source") or actor_type)
    target_intent = str(payload.get("target_intent") or "advanced").strip().lower()
    acting_role_id = payload.get("acting_role_id")
    prepared_related_transition = None
    if acting_role_id is not None:
        from ....services.related_role_ats_transition import (
            prepare_related_role_ats_transition,
        )

        prepared_related_transition = prepare_related_role_ats_transition(
            db,
            acting_role_id=int(acting_role_id),
            application=app,
        )
    with strict_workable_writes():
        result = provider.move_application(
            candidate_id=str(app.bullhorn_job_submission_id),
            target_stage=target_intent,
            role=getattr(app, "role", None),
        )
    _raise_if_failed(result, default_action="move")
    if prepared_related_transition is not None:
        from ....services.related_role_ats_transition import (
            advance_prepared_related_role_transition,
        )
        from ....services.sister_role_service import related_role_advance_note

        acting_role = advance_prepared_related_role_transition(
            prepared_related_transition
        )
        if acting_role is not None:
            candidate = getattr(app, "candidate", None)
            bullhorn_candidate_id = str(
                getattr(candidate, "bullhorn_candidate_id", None) or ""
            ).strip()
            if bullhorn_candidate_id:
                # Bullhorn has no idempotency key for Note creation. The
                # persisted related stage suppresses ordinary redelivery, but
                # a crash after provider acceptance is an explicitly
                # at-least-once informational-note boundary.
                note_result = provider.post_note(
                    candidate_id=bullhorn_candidate_id,
                    member_id="",
                    body=related_role_advance_note(
                        acting_role, getattr(app, "role", None)
                    ),
                    role=getattr(app, "role", None),
                )
                _raise_if_failed(note_result, default_action="note")
    append_application_event(
        db,
        app=app,
        event_type="bullhorn_moved",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason or "Candidate handed back to Bullhorn",
        metadata={
            "bullhorn_status": result.get("config", {}).get("remote_status"),
            "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
            "taali_intent": target_intent,
        },
    )
    if target_intent in {"advanced", "advance", "skip_advanced"}:
        transition_stage(
            db,
            app=app,
            to_stage="advanced",
            source=source,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason or "Handed back to Bullhorn",
            metadata={"bullhorn_status": result.get("config", {}).get("remote_status")},
            idempotency_key=f"bullhorn_handback:{app.id}",
        )
    db.commit()
    return {"status": "ok", "application_id": application_id}


def run_manual_outcome(db: Session, org: Organization, app: CandidateApplication, payload: dict) -> dict:
    """Bullhorn outcome sync — analogue of ``_op_manual_outcome``.

    Reject writes the org's rejected-category status; re-open writes a non-reject
    status back (Bullhorn has no first-class un-reject). Local outcome already
    committed in the route; this is the (retried) remote write only.
    """
    from ....domains.assessments_runtime.pipeline_service import append_application_event
    from ....services.workable_actions_service import strict_workable_writes

    application_id = int(app.id)
    if not app.bullhorn_job_submission_id:
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    provider = _bullhorn_provider(db, org, app)
    if provider is None:
        return {"status": "skipped", "reason": "not_connected", "application_id": application_id}

    target_outcome = payload.get("target_outcome")
    reason = payload.get("reason")
    user_id = payload.get("user_id")
    actor_type = str(payload.get("actor_type") or "recruiter")
    actor_id = payload.get("actor_id", user_id)
    with strict_workable_writes():
        if target_outcome == "open":
            result = provider.revert_application(app=app, role=None)
            event_type = "bullhorn_reverted"
        else:
            result = provider.reject_application(app=app, role=None, reason=reason)
            event_type = "bullhorn_rejected"
    _raise_if_failed(result, default_action="move")
    from ....services.ats_writeback_state import set_outcome_writeback_state

    set_outcome_writeback_state(
        app,
        provider="bullhorn",
        status="confirmed",
        target_outcome=str(target_outcome or ""),
        remote_status=result.get("config", {}).get("remote_status"),
    )
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason or "Bullhorn outcome synced",
        metadata={
            "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
            "target_outcome": target_outcome,
            "bullhorn_status": result.get("config", {}).get("remote_status"),
        },
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}


def run_post_note(db: Session, org: Organization, app: CandidateApplication, payload: dict) -> dict:
    """Bullhorn free-form note — analogue of ``_op_post_note``.

    Posts a Note about the candidate (personReference + jobOrder). This op is not
    strict-gated, so a failed post is turned into a retriable
    ``WorkableWritebackError`` for the shell to retry.
    """
    from ....domains.assessments_runtime.pipeline_service import append_application_event

    application_id = int(app.id)
    body = str(payload.get("body") or "").strip()
    user_id = payload.get("user_id")
    actor_type = str(payload.get("actor_type") or "recruiter")
    actor_id = payload.get("actor_id", user_id)
    candidate = getattr(app, "candidate", None)
    bullhorn_candidate_id = str(getattr(candidate, "bullhorn_candidate_id", None) or "").strip()
    if not app.bullhorn_job_submission_id or not bullhorn_candidate_id or not body:
        return {"status": "skipped", "reason": "not_linked_or_empty", "application_id": application_id}
    provider = _bullhorn_provider(db, org, app)
    if provider is None:
        return {"status": "skipped", "reason": "not_connected", "application_id": application_id}

    result = provider.post_note(
        candidate_id=bullhorn_candidate_id,
        member_id="",
        body=body,
        role=getattr(app, "role", None),
    )
    _raise_if_failed(result, default_action="note")
    append_application_event(
        db,
        app=app,
        event_type="bullhorn_note_posted",
        actor_type=actor_type,
        actor_id=actor_id,
        reason="Note posted to Bullhorn",
        metadata={"bullhorn_candidate_id": bullhorn_candidate_id},
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}
