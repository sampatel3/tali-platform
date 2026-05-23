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
from .document_service import sanitize_text_for_storage
from .pre_screen_decision_emitter import queue_pre_screen_reject
from .pre_screening_service import (
    evaluate_auto_reject_decision,
    mark_auto_reject_state,
    refresh_pre_screening_fields,
)
from .workable_actions_service import disqualify_candidate_in_workable


def _candidate_label(app: CandidateApplication) -> str:
    candidate = getattr(app, "candidate", None)
    name = sanitize_text_for_storage(str(getattr(candidate, "full_name", None) or "").strip())
    if name:
        return name
    email = sanitize_text_for_storage(str(getattr(candidate, "email", None) or "").strip())
    if email:
        return email
    return "Candidate"


def run_auto_reject_if_needed(
    *,
    db,
    org: Organization | None,
    app: CandidateApplication,
    role: Role | None,
    actor_type: str,
    actor_id: int | None = None,
) -> dict[str, Any]:
    refresh_pre_screening_fields(app)
    decision = evaluate_auto_reject_decision(app, org=org, role=role, db=db)
    if not decision.get("should_trigger"):
        mark_auto_reject_state(
            app,
            state=str(decision.get("state") or "skipped"),
            reason=decision.get("reason"),
            triggered=False,
        )
        return {**decision, "performed": False}

    # Per-role HITL gate. When ``auto_reject`` is False (the default),
    # we don't disqualify in Workable directly — instead surface a Decision
    # Hub card so the recruiter approves the reject manually. The original
    # design here deferred this to the next agent cycle, but the agent's
    # cohort planner never surveyed "below-threshold" candidates so 270
    # ended up stranded in prod. Emit the decision directly now (pre-screen
    # is deterministic; no agent reasoning needed).
    if role is not None and not bool(getattr(role, "auto_reject", False)):
        snapshot = decision.get("snapshot") if isinstance(decision.get("snapshot"), dict) else {}
        config = decision.get("config") if isinstance(decision.get("config"), dict) else {}
        queued = queue_pre_screen_reject(
            db,
            organization_id=int(app.organization_id),
            role=role,
            application=app,
            pre_screen_score=snapshot.get("pre_screen_score"),
            threshold=config.get("threshold_100"),
            evidence={
                "cv_fit_score": snapshot.get("cv_fit_score"),
                "requirements_fit_score": snapshot.get("requirements_fit_score"),
            },
        )
        # Only claim Decision Hub review when a card was actually created.
        # ``queue_pre_screen_reject`` returns None for agent-off roles (or
        # on emit failure) — in that case nothing surfaces in the Hub, so
        # reporting "awaiting_recruiter_approval" would be a silent lie.
        # Fall back to "skipped" so the state reflects the true outcome.
        if queued is None:
            reason = (
                "Below pre-screen threshold; auto_reject is off and no "
                "Decision Hub card was created (role not under agent "
                "management)."
            )
            mark_auto_reject_state(app, state="skipped", reason=reason, triggered=False)
            return {**decision, "performed": False, "state": "skipped", "reason": reason}
        mark_auto_reject_state(
            app,
            state="awaiting_recruiter_approval",
            reason=(
                "Below pre-screen threshold; auto_reject is off so the "
                "candidate is left open for Decision Hub review."
            ),
            triggered=False,
        )
        return {
            **decision,
            "performed": False,
            "state": "awaiting_recruiter_approval",
            "reason": (
                "Below pre-screen threshold; auto_reject is off — leaving "
                "open for Decision Hub review."
            ),
        }

    # Candidate-linkage gate. The Workable write-back below disqualifies by
    # ``workable_candidate_id``; an unlinked candidate hits a guaranteed
    # ``missing_candidate_id`` failure. ``evaluate_auto_reject_decision``
    # lets unlinked candidates through when the role is agentic_eligible, so
    # don't attempt the Workable round-trip — surface a Decision Hub card
    # instead (or skip cleanly if no card can be created). (Codex #229)
    if role is not None and not getattr(app, "workable_candidate_id", None):
        snapshot = decision.get("snapshot") if isinstance(decision.get("snapshot"), dict) else {}
        config = decision.get("config") if isinstance(decision.get("config"), dict) else {}
        queued = queue_pre_screen_reject(
            db,
            organization_id=int(app.organization_id),
            role=role,
            application=app,
            pre_screen_score=snapshot.get("pre_screen_score"),
            threshold=config.get("threshold_100"),
            evidence={
                "cv_fit_score": snapshot.get("cv_fit_score"),
                "requirements_fit_score": snapshot.get("requirements_fit_score"),
            },
        )
        if queued is None:
            reason = (
                "Below pre-screen threshold but candidate is not linked to "
                "Workable and no Decision Hub card was created; skipping "
                "auto-reject write-back."
            )
            mark_auto_reject_state(app, state="skipped", reason=reason, triggered=False)
            return {**decision, "performed": False, "state": "skipped", "reason": reason}
        reason = (
            "Below pre-screen threshold; candidate not linked to Workable — "
            "surfaced for Decision Hub review instead of write-back."
        )
        mark_auto_reject_state(
            app, state="awaiting_recruiter_approval", reason=reason, triggered=False
        )
        return {
            **decision,
            "performed": False,
            "state": "awaiting_recruiter_approval",
            "reason": reason,
        }

    if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        reason = "Workable is not connected for auto reject write-back"
        mark_auto_reject_state(app, state="failed", reason=reason, triggered=False)
        return {**decision, "performed": False, "state": "failed", "reason": reason}

    config = decision.get("config") if isinstance(decision.get("config"), dict) else {}
    member_id = sanitize_text_for_storage(str(config.get("workable_actor_member_id") or "").strip()) or None
    if not member_id:
        reason = "Auto reject member is not configured"
        mark_auto_reject_state(app, state="failed", reason=reason, triggered=False)
        return {**decision, "performed": False, "state": "failed", "reason": reason}

    result = disqualify_candidate_in_workable(
        org=org,
        app=app,
        role=role,
        reason=decision.get("reason"),
        note_template=config.get("auto_reject_note_template"),
        threshold_100=config.get("threshold_100"),
        withdrew=False,
    )
    if not result.get("success"):
        reason = sanitize_text_for_storage(
            str(result.get("message") or decision.get("reason") or "Failed to disqualify candidate in Workable")
        )
        mark_auto_reject_state(app, state="failed", reason=reason, triggered=False)
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_failed",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata={
                "action": result.get("action"),
                "code": result.get("code"),
                "pre_screen_score": decision.get("snapshot", {}).get("pre_screen_score"),
                "threshold_100": config.get("threshold_100"),
                "workable_candidate_id": app.workable_candidate_id,
            },
        )
        append_application_event(
            db,
            app=app,
            event_type="auto_reject_failed",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            metadata={
                "pre_screen_score": decision.get("snapshot", {}).get("pre_screen_score"),
                "threshold_100": config.get("threshold_100"),
                "workable_candidate_id": app.workable_candidate_id,
            },
        )
        return {**decision, "performed": False, "state": "failed", "reason": reason, "workable_result": result}

    ensure_pipeline_fields(app)
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason="Auto-rejected from Workable pre-screen",
    )
    append_application_event(
        db,
        app=app,
        event_type="workable_disqualified",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=decision.get("reason"),
        metadata={
            "pre_screen_score": decision.get("snapshot", {}).get("pre_screen_score"),
            "threshold_100": config.get("threshold_100"),
            "workable_candidate_id": app.workable_candidate_id,
            "workable_actor_member_id": member_id,
            "workable_disqualify_reason_id": config.get("workable_disqualify_reason_id"),
        },
    )
    append_application_event(
        db,
        app=app,
        event_type="auto_rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=decision.get("reason"),
        metadata={
            "pre_screen_score": decision.get("snapshot", {}).get("pre_screen_score"),
            "cv_fit_score": decision.get("snapshot", {}).get("cv_fit_score"),
            "requirements_fit_score": decision.get("snapshot", {}).get("requirements_fit_score"),
            "threshold_100": config.get("threshold_100"),
            "workable_candidate_id": app.workable_candidate_id,
            "workable_actor_member_id": member_id,
            "workable_disqualify_reason_id": config.get("workable_disqualify_reason_id"),
        },
    )
    mark_auto_reject_state(
        app,
        state="rejected",
        reason=decision.get("reason"),
        triggered=True,
    )
    return {**decision, "performed": True, "state": "rejected", "workable_result": result}
