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
from .workable_actions_service import (
    disqualify_candidate_in_workable,
    workable_job_state,
    workable_job_syncable,
)


def _candidate_label(app: CandidateApplication) -> str:
    candidate = getattr(app, "candidate", None)
    name = sanitize_text_for_storage(str(getattr(candidate, "full_name", None) or "").strip())
    if name:
        return name
    email = sanitize_text_for_storage(str(getattr(candidate, "email", None) or "").strip())
    if email:
        return email
    return "Candidate"


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
) -> dict[str, Any]:
    """Reject a single candidate the agent can't evaluate for lack of usable
    CV text — either no CV file at all (``reason="No CV on file"``) or a file
    that couldn't be read (``reason="CV could not be read"``). The caller
    picks the reason so the Workable note + event trail stay honest about the
    cause.

    Mirrors the success path of ``run_auto_reject_if_needed`` (Workable
    disqualify first, then flip the local outcome only on success, so the two
    never diverge), minus the pre-screen threshold logic. When the candidate
    isn't linked to Workable (or the org can't write), we still apply the
    local reject; there's simply nothing to disqualify upstream.

    Returns ``{"performed": True, ...}`` on success or
    ``{"performed": False, "reason": <message>}`` when the Workable write-back
    failed (the caller leaves the candidate open and reports the failure). Any
    DB writes made here (events) are left for the caller to commit/rollback.
    """
    workable_linked = bool(getattr(app, "workable_candidate_id", None))
    org_writeable = bool(
        org
        and getattr(org, "workable_connected", False)
        and getattr(org, "workable_access_token", None)
        and getattr(org, "workable_subdomain", None)
    )
    wrote_to_workable = workable_linked and org_writeable

    if wrote_to_workable:
        result = disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=role,
            reason=reason,
            withdrew=False,
        )
        if not result.get("success"):
            msg = sanitize_text_for_storage(
                str(result.get("message") or "Failed to disqualify candidate in Workable")
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
            return {"performed": False, "reason": msg, "workable_result": result}

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
    return {"performed": True, "reason": reason, "workable_written": wrote_to_workable}


def _divert_pre_screen_reject_to_card(
    db,
    *,
    app: CandidateApplication,
    role: Role | None,
    decision: dict[str, Any],
    carded_reason: str,
    fallback_state: str,
    fallback_reason: str,
) -> dict[str, Any]:
    """Surface a below-threshold candidate as a Decision Hub pre-screen-reject
    card when the direct Workable disqualify can't run (auto_reject off, or the
    candidate isn't Workable-linked) OR *failed* (e.g. a 403 on a Workable job
    this actor member can't action — DeepLight role 53).

    The card is the same agent-HITL surface ``auto_reject``-off roles use, so a
    deterministic reject the agent couldn't *execute* is never silently
    stranded: the recruiter still sees it. Critically, the resulting pending
    decision is what lets ``pre_screen_reject_sweep``'s ``~has_pending`` guard
    stop re-dispatching the failing write-back — it turns an endless per-tick
    403 retry storm into a single attempt followed by one card.

    ``queue_pre_screen_reject`` returns None — and we fall back to
    ``fallback_state``/``fallback_reason`` — for non-agent roles, already
    cv_match-scored candidates (the agent owns those), or a passed pre-screen,
    so those keep their honest terminal state ('skipped'/'failed').
    """
    snapshot = decision.get("snapshot") if isinstance(decision.get("snapshot"), dict) else {}
    config = decision.get("config") if isinstance(decision.get("config"), dict) else {}
    queued = (
        queue_pre_screen_reject(
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
        if role is not None
        else None
    )
    if queued is None:
        mark_auto_reject_state(app, state=fallback_state, reason=fallback_reason, triggered=False)
        return {**decision, "performed": False, "state": fallback_state, "reason": fallback_reason}
    mark_auto_reject_state(
        app, state="awaiting_recruiter_approval", reason=carded_reason, triggered=False
    )
    return {
        **decision,
        "performed": False,
        "state": "awaiting_recruiter_approval",
        "reason": carded_reason,
    }


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

    # Per-role HITL gate. We disqualify in Workable directly ONLY when the role
    # explicitly opted in — the full ``auto_reject`` toggle OR the narrower
    # ``auto_reject_pre_screen`` one (this function IS the pre-screen path:
    # ``evaluate_auto_reject_decision`` defers to full scoring once a cv_match
    # score exists) — AND the decision is ``auto_disqualify_eligible`` (org
    # Workable switch or agent-managed role).
    # Otherwise — including agent-off roles, where the reject is still a valid
    # deterministic decision — surface a Decision Hub card for manual review.
    # This is what lets a below-threshold candidate reach the Hub without the
    # agent on, while NEVER triggering a new irreversible Workable write-back.
    # (The original design deferred this to the agent cycle, but the cohort
    # planner never surveyed below-threshold candidates so 270 stranded in prod.)
    auto_disqualify_eligible = bool(decision.get("auto_disqualify_eligible", True))
    auto_reject_opted_in = bool(getattr(role, "auto_reject", False)) or bool(
        getattr(role, "auto_reject_pre_screen", False)
    )
    if role is not None and not (auto_reject_opted_in and auto_disqualify_eligible):
        # Not eligible for direct Workable disqualify → recruiter approves the
        # reject manually; surface a Decision Hub card instead.
        return _divert_pre_screen_reject_to_card(
            db,
            app=app,
            role=role,
            decision=decision,
            carded_reason=(
                "Below pre-screen threshold; auto_reject is off so the "
                "candidate is left open for Decision Hub review."
            ),
            fallback_state="skipped",
            fallback_reason=(
                "Below pre-screen threshold; auto_reject is off and no "
                "Decision Hub card was created (role not under agent "
                "management)."
            ),
        )

    # Candidate-linkage gate. The Workable write-back below disqualifies by
    # ``workable_candidate_id``; an unlinked candidate hits a guaranteed
    # ``missing_candidate_id`` failure. ``evaluate_auto_reject_decision``
    # lets unlinked candidates through when the role is agentic_eligible, so
    # don't attempt the Workable round-trip — surface a Decision Hub card
    # instead (or skip cleanly if no card can be created). (Codex #229)
    if role is not None and not getattr(app, "workable_candidate_id", None):
        # Unlinked candidate: the disqualify-by-id below would be a guaranteed
        # miss. Surface a card instead (or skip cleanly if none can be made).
        return _divert_pre_screen_reject_to_card(
            db,
            app=app,
            role=role,
            decision=decision,
            carded_reason=(
                "Below pre-screen threshold; candidate not linked to Workable "
                "— surfaced for Decision Hub review instead of write-back."
            ),
            fallback_state="skipped",
            fallback_reason=(
                "Below pre-screen threshold but candidate is not linked to "
                "Workable and no Decision Hub card was created; skipping "
                "auto-reject write-back."
            ),
        )

    # Archived/closed/draft Workable req: Workable 403s any disqualify there, so
    # don't attempt the sync (and don't strand the candidate as a card). Reject
    # locally instead — same decision, just not synced to Workable — so the
    # candidate resolves to 'rejected' rather than waiting forever.
    if role is not None and not workable_job_syncable(role):
        ensure_pipeline_fields(app)
        transition_outcome(
            db,
            app=app,
            to_outcome="rejected",
            actor_type=actor_type,
            actor_id=actor_id,
            reason="Auto-rejected from pre-screen — Workable req not live (Taali-only)",
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
                "threshold_100": (decision.get("config") or {}).get("threshold_100"),
                "workable_synced": False,
                "workable_job_state": workable_job_state(role),
                "skip_reason": "workable_req_not_live",
            },
        )
        mark_auto_reject_state(
            app,
            state="rejected",
            reason="Below pre-screen threshold; Workable req not live — rejected in Taali only.",
            triggered=True,
        )
        return {
            **decision,
            "performed": True,
            "state": "rejected",
            "workable_synced": False,
        }

    if not org or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        reason = "Workable is not connected for auto reject write-back"
        # Can't disqualify upstream, but an agent-managed role should still
        # surface the reject as a card rather than strand it as 'failed'.
        return _divert_pre_screen_reject_to_card(
            db,
            app=app,
            role=role,
            decision=decision,
            carded_reason=(
                "Below pre-screen threshold; Workable is not connected for "
                "write-back — surfaced for Decision Hub review."
            ),
            fallback_state="failed",
            fallback_reason=reason,
        )

    config = decision.get("config") if isinstance(decision.get("config"), dict) else {}
    member_id = sanitize_text_for_storage(str(config.get("workable_actor_member_id") or "").strip()) or None
    if not member_id:
        reason = "Auto reject member is not configured"
        return _divert_pre_screen_reject_to_card(
            db,
            app=app,
            role=role,
            decision=decision,
            carded_reason=(
                "Below pre-screen threshold; no Workable auto-reject member is "
                "configured — surfaced for Decision Hub review."
            ),
            fallback_state="failed",
            fallback_reason=reason,
        )

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
        # The disqualify failed (e.g. a 403 on a Workable job this actor member
        # can't action). Don't strand the candidate as 'failed' — that left
        # 1,100+ DeepLight role-53 candidates invisible while the sweep retried
        # the same 403 every tick. Surface a Decision Hub card so the reject is
        # visible/actionable; the resulting pending decision is what stops the
        # sweep re-dispatching (its ``~has_pending`` guard). Falls back to the
        # original 'failed' terminal state when no card can be created.
        outcome = _divert_pre_screen_reject_to_card(
            db,
            app=app,
            role=role,
            decision=decision,
            carded_reason=(
                "Below pre-screen threshold; the Workable auto-disqualify "
                f"failed ({reason or 'write-back error'}) — surfaced for "
                "Decision Hub review."
            ),
            fallback_state="failed",
            fallback_reason=reason,
        )
        outcome["workable_result"] = result
        return outcome

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
