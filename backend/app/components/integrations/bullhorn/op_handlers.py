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

    Under strict mode the provider already raised; this also normalizes private
    provider writes so a failed critical operation is surfaced rather than
    silently succeeding. Optional movement summaries catch this error locally.
    """
    if result.get("success") or result.get("skipped"):
        return
    code = str(result.get("code") or "api_error")
    raise WorkableWritebackError(
        action=str(result.get("action") or default_action),
        code=code,
        message=str(result.get("message") or "Bullhorn write failed"),
        retriable=(code == "api_error"),
    )


def _record_bullhorn_movement_note_failure(
    db: Session,
    *,
    app: CandidateApplication,
    application_id: int,
    action: str = "related_role_movement_note",
    role_id: int | None = None,
    ats_application_id: int | None = None,
) -> None:
    """Record an optional-note miss without replaying a confirmed movement."""
    from ....domains.assessments_runtime.pipeline_service import (
        append_application_event,
    )

    try:
        db.rollback()
    except Exception:
        logger.exception(
            "could not reset session after Bullhorn movement-note failure "
            "application_id=%s",
            application_id,
        )
        return
    try:
        append_application_event(
            db,
            app=app,
            role_id=role_id,
            event_type="bullhorn_movement_note_failed",
            actor_type="system",
            reason=(
                "The candidate movement was confirmed, but its Bullhorn "
                "summary was not posted."
            ),
            metadata={
                "ats": "bullhorn",
                "action": action,
                **(
                    {"acting_role_id": int(role_id)}
                    if role_id is not None
                    else {}
                ),
                **(
                    {"ats_application_id": int(ats_application_id)}
                    if ats_application_id is not None
                    else {}
                ),
            },
        )
        db.commit()
    except Exception:
        logger.exception(
            "could not persist Bullhorn movement-note failure "
            "application_id=%s",
            application_id,
        )
        try:
            db.rollback()
        except Exception:
            logger.exception(
                "could not roll back Bullhorn movement-note failure "
                "application_id=%s",
                application_id,
            )


def _post_confirmed_related_role_bullhorn_note(
    db: Session,
    org: Organization,
    app: CandidateApplication,
    *,
    event_app: CandidateApplication | None = None,
    acting_role: object,
    actor_type: str,
    actor_id: int | None,
) -> dict:
    """Post fixed related-role copy after a confirmed outbound Bullhorn move.

    This is intentionally not exposed through the shared op dispatch and does
    not accept caller-owned note text.
    """
    from ....domains.assessments_runtime.pipeline_service import (
        append_application_event,
    )
    from ....services.sister_role_service import related_role_advance_note

    application_id = int(app.id)
    if (
        acting_role is None
        or int(getattr(acting_role, "ats_owner_role_id", 0) or 0)
        != int(app.role_id or 0)
    ):
        return {
            "status": "skipped",
            "reason": "invalid_related_role",
            "application_id": application_id,
        }
    candidate = getattr(app, "candidate", None)
    bullhorn_candidate_id = str(
        getattr(candidate, "bullhorn_candidate_id", None) or ""
    ).strip()
    if not app.bullhorn_job_submission_id or not bullhorn_candidate_id:
        return {
            "status": "skipped",
            "reason": "not_linked",
            "application_id": application_id,
        }
    provider = _bullhorn_provider(db, org, app)
    if provider is None:
        return {
            "status": "skipped",
            "reason": "not_connected",
            "application_id": application_id,
        }

    result = provider.post_note(
        candidate_id=bullhorn_candidate_id,
        member_id="",
        body=related_role_advance_note(acting_role, getattr(app, "role", None)),
        role=getattr(app, "role", None),
        trusted_role_values=tuple(
            value
            for value in (
                str(getattr(acting_role, "name", None) or "").strip(),
                str(getattr(getattr(app, "role", None), "name", None) or "").strip(),
            )
            if value
        ),
    )
    _raise_if_failed(result, default_action="note")
    append_application_event(
        db,
        app=event_app or app,
        role_id=int(acting_role.id),
        event_type="bullhorn_note_posted",
        actor_type=actor_type,
        actor_id=actor_id,
        reason="Related-role movement summary posted to Bullhorn",
        metadata={
            "acting_role_id": int(acting_role.id),
            "ats_application_id": int(app.id),
            "bullhorn_candidate_id": bullhorn_candidate_id,
        },
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}


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
    from ....services.related_role_action_service import (
        RelatedRoleActionContractError,
        related_role_ats_action_state,
        resolve_related_role_ats_action_context,
        transition_related_role_stage_action,
    )
    from ....services.workable_actions_service import strict_workable_writes

    application_id = int(app.id)
    if not app.bullhorn_job_submission_id:
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    provider = _bullhorn_provider(db, org, app)
    if provider is None:
        return {"status": "skipped", "reason": "not_connected", "application_id": application_id}

    reason = payload.get("reason")
    user_id = payload.get("user_id")
    actor_type = str(payload.get("actor_type") or "recruiter")
    actor_id = payload.get("actor_id", user_id)
    source = str(payload.get("source") or actor_type)
    target_intent = str(payload.get("target_intent") or "advanced").strip().lower()
    try:
        related_context = resolve_related_role_ats_action_context(
            db,
            organization_id=int(org.id),
            ats_application=app,
            acting_role_id=payload.get("acting_role_id"),
            source_application_id=payload.get("role_application_id"),
        )
    except RelatedRoleActionContractError as exc:
        raise WorkableWritebackError(
            action="move",
            code="related_role_context_invalid",
            message=str(exc),
            retriable=False,
        ) from exc
    event_app = (
        related_context.source_application if related_context is not None else app
    )
    logical_role_id = (
        int(related_context.role.id)
        if related_context is not None
        else int(app.role_id)
    )

    def blocked_related_move(codes: list[str], *, reason_code: str) -> dict:
        append_application_event(
            db,
            app=event_app,
            role_id=logical_role_id,
            event_type="ats_writeback_restricted",
            actor_type=actor_type,
            actor_id=actor_id,
            target_stage=target_intent,
            effect_status="blocked",
            reason="Related-role ATS move was blocked at execution time",
            metadata={
                "acting_role_id": logical_role_id,
                "ats_application_id": int(app.id),
                "provider": "bullhorn",
                "op_type": "move_stage",
                "restriction_codes": codes,
                "target_stage": target_intent,
            },
            idempotency_key=(
                f"ats_move_blocked:{payload.get('job_run_id')}"
                if payload.get("job_run_id") is not None
                else (
                    f"ats_move_blocked:bullhorn:{event_app.id}:"
                    f"{logical_role_id}:{target_intent}:{reason_code}"
                )
            ),
        )
        db.commit()
        return {
            "status": "skipped",
            "reason": reason_code,
            "application_id": application_id,
        }

    def reconcile_confirmed_advance(*, already_at_target: bool, remote_status=None) -> None:
        event_reason = (
            "Already at the Bullhorn hand-off status"
            if already_at_target
            else (reason or "Handed back to Bullhorn")
        )
        event_key = (
            f"bullhorn_handback_reconcile:{event_app.id}:{logical_role_id}"
            if already_at_target
            else f"bullhorn_handback:{event_app.id}:{logical_role_id}"
        )
        metadata = {
            "bullhorn_status": remote_status,
            "ats_application_id": int(app.id),
            **(
                {"acting_role_id": logical_role_id}
                if related_context is not None
                else {}
            ),
        }
        if related_context is not None:
            transition_related_role_stage_action(
                db,
                application=event_app,
                acting_role_id=logical_role_id,
                to_stage="advanced",
                source=source,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=event_reason,
                metadata=metadata,
                idempotency_key=event_key,
            )
            return
        transition_stage(
            db,
            app=app,
            to_stage="advanced",
            source=source,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=event_reason,
            metadata=metadata,
            idempotency_key=event_key,
        )

    if related_context is not None:
        action_state = related_role_ats_action_state(related_context)
        local_codes = list(action_state["local_codes"])
        if local_codes == ["role_pipeline_stage_advanced"]:
            return {
                "status": "skipped",
                "reason": "role_already_advanced",
                "application_id": application_id,
            }
        if local_codes:
            return blocked_related_move(
                local_codes,
                reason_code="related_role_application_resolved",
            )
        hard_codes = list(action_state["hard_restriction_codes"])
        if hard_codes:
            return blocked_related_move(
                hard_codes,
                reason_code="related_role_ats_write_restricted",
            )
        if bool(action_state["post_handover"]):
            reconcile_confirmed_advance(
                already_at_target=True,
                remote_status=getattr(app, "bullhorn_status", None),
            )
            db.commit()
            return {
                "status": "skipped",
                "reason": "already_at_target",
                "application_id": application_id,
            }
    with strict_workable_writes():
        result = provider.move_application(
            candidate_id=str(app.bullhorn_job_submission_id),
            target_stage=target_intent,
            role=getattr(app, "role", None),
        )
    _raise_if_failed(result, default_action="move")
    if result.get("skipped") or result.get("code") == "already_at_target":
        if target_intent in {"advanced", "advance", "skip_advanced"}:
            reconcile_confirmed_advance(
                already_at_target=True,
                remote_status=result.get("config", {}).get("remote_status"),
            )
            db.commit()
        return {
            "status": "skipped",
            "reason": "already_at_target",
            "application_id": application_id,
        }
    append_application_event(
        db,
        app=event_app,
        role_id=logical_role_id,
        event_type="bullhorn_moved",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason or "Candidate handed back to Bullhorn",
        target_stage=(
            str(result.get("config", {}).get("remote_status") or "").strip()
            or target_intent
        ),
        effect_status="confirmed",
        metadata={
            "bullhorn_status": result.get("config", {}).get("remote_status"),
            "bullhorn_job_submission_id": app.bullhorn_job_submission_id,
            "taali_intent": target_intent,
            "ats_application_id": int(app.id),
            **(
                {"acting_role_id": logical_role_id}
                if related_context is not None
                else {}
            ),
        },
    )
    if target_intent in {"advanced", "advance", "skip_advanced"}:
        reconcile_confirmed_advance(
            already_at_target=False,
            remote_status=result.get("config", {}).get("remote_status"),
        )
    # Checkpoint the confirmed status move before the optional movement note.
    # A note failure must not retry or invalidate the provider movement.
    db.commit()
    acting_role_id = payload.get("acting_role_id")
    confirmed_outbound_advance = target_intent in {
        "advanced",
        "advance",
        "skip_advanced",
    }
    if confirmed_outbound_advance and acting_role_id is not None:
        from ....models.role import Role

        acting_role = db.get(Role, int(acting_role_id))
        if (
            acting_role is not None
            and int(acting_role.ats_owner_role_id or 0) == int(app.role_id)
        ):
            try:
                note_result = _post_confirmed_related_role_bullhorn_note(
                    db,
                    org,
                    app,
                    event_app=event_app,
                    acting_role=acting_role,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )
                if note_result.get("status") != "ok":
                    raise WorkableWritebackError(
                        action="note",
                        code=str(note_result.get("reason") or "note_skipped"),
                        message="Related-role movement note was not posted",
                        retriable=False,
                    )
            except Exception as exc:
                logger.warning(
                    "related-role Bullhorn movement note failed after confirmed move "
                    "application_id=%s error_type=%s",
                    application_id,
                    type(exc).__name__,
                )
                _record_bullhorn_movement_note_failure(
                    db,
                    app=event_app,
                    application_id=application_id,
                    role_id=logical_role_id,
                    ats_application_id=int(app.id),
                )
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
            # Bullhorn Note creation is non-idempotent. Keep it outside the
            # critical status operation so the confirmed status can be
            # checkpointed before any optional summary is attempted.
            result = provider.reject_application(
                app=app,
                role=None,
                reason=reason,
                include_movement_note=False,
            )
            event_type = "bullhorn_rejected"
    _raise_if_failed(result, default_action="move")
    from ....services.ats_writeback_state import set_outcome_writeback_state

    if result.get("skipped") or result.get("code") == "already_at_target":
        set_outcome_writeback_state(
            app,
            provider="bullhorn",
            status="confirmed",
            target_outcome=str(target_outcome or ""),
            remote_status=result.get("config", {}).get("remote_status"),
        )
        db.commit()
        return {
            "status": "skipped",
            "reason": "already_at_target",
            "application_id": application_id,
        }
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
    if target_outcome != "open":
        # The commit above is the at-most-once boundary. If an acks-late worker
        # dies during or after Note creation, redelivery observes the durable
        # exact-target Bullhorn status and returns through the silent no-op path
        # above instead of replaying either the status write or the note.
        try:
            note_result = provider.post_rejection_movement_note(
                app=app,
                role=getattr(app, "role", None),
                reason=reason,
                movement_result=result,
            )
            if (
                note_result.get("config", {}).get("movement_note_status")
                == "failed"
            ):
                _record_bullhorn_movement_note_failure(
                    db,
                    app=app,
                    application_id=application_id,
                    action="manual_rejection_movement_note",
                )
        except Exception as exc:  # pragma: no cover - defensive provider edge
            logger.warning(
                "Bullhorn manual rejection summary failed after confirmed status "
                "application_id=%s error_type=%s",
                application_id,
                type(exc).__name__,
            )
            _record_bullhorn_movement_note_failure(
                db,
                app=app,
                application_id=application_id,
                action="manual_rejection_movement_note",
            )
    return {"status": "ok", "application_id": application_id}


def run_post_note(db: Session, org: Organization, app: CandidateApplication, payload: dict) -> dict:
    """Fail closed for every legacy standalone Bullhorn note operation."""
    return {
        "status": "skipped",
        "reason": "standalone_ats_notes_disabled",
        "application_id": int(app.id),
    }
