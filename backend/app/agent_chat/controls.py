"""Agent-control tools — activate / pause the role's agent and adjust its
settings from the chat.

Mirrors the role-update PATCH in ``assessments_runtime/roles_management_routes.py``
(budget gate on activate, clear-pause on resume, auto-sync star, an immediate
cycle kick) and reuses the SAME helpers — ``budget_guard.resume_if_under_budget``
and the complete ``agent_cohort_tick_role`` task — so steering from chat and from the
settings UI stay in lockstep. Commits before kicking a cycle so the worker
sees the new state (same ordering the route uses).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from ..services.agent_policy_settings import (
    GRANULAR_AUTOMATION_FIELDS,
    SCORE_ONLY_ROLE_AUTOMATION_MESSAGE,
    activation_policy_values,
    effective_agent_policy,
    role_is_score_only,
    role_automation_enabled,
)
from ..services.role_change_audit import (
    ROLE_CHANGE_ACTION_AGENT_ENABLED,
    ROLE_CHANGE_ACTION_AGENT_PAUSED,
    ROLE_CHANGE_ACTION_AGENT_RESUMED,
    ROLE_CHANGE_ACTION_UPDATED,
    add_role_change_event,
    capture_role_change_snapshot,
)
from ..services.role_concurrency import bump_role_version

logger = logging.getLogger("taali.agent_chat.controls")

_ACTIVATE = {"activate", "resume", "enable", "start", "restart", "on", "unpause"}
_PAUSE = {"pause", "stop", "hold", "suspend"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state(
    role: Role,
    *,
    workspace_pause: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role_paused = role.agent_paused_at is not None
    workspace_paused = bool(
        workspace_pause is not None and workspace_pause.get("paused")
    )
    effective_paused = bool(role.agentic_mode_enabled) and (
        workspace_paused or role_paused
    )
    pause_scope = (
        "workspace"
        if effective_paused and workspace_paused
        else ("role" if effective_paused and role_paused else None)
    )
    return {
        "version": int(role.version or 1),
        "enabled": bool(role.agentic_mode_enabled),
        # Legacy consumers read ``paused``/``paused_reason``. Keep those fields
        # effective so they cannot render a locally runnable role as active
        # under the workspace overlay; expose local desired state separately.
        "paused": effective_paused,
        "paused_reason": (
            workspace_pause.get("reason")
            if pause_scope == "workspace" and workspace_pause is not None
            else (role.agent_paused_reason if pause_scope == "role" else None)
        ),
        "effective_paused": effective_paused,
        "pause_scope": pause_scope,
        "role_paused": role_paused,
        "role_paused_at": role.agent_paused_at,
        "role_paused_reason": role.agent_paused_reason,
        "workspace_paused": workspace_paused,
        "monthly_budget_cents": role.monthly_usd_budget_cents,
        "auto_reject": bool(role.auto_reject),
        "auto_reject_pre_screen": bool(role.auto_reject_pre_screen),
        "auto_promote": bool(role.auto_promote),
        "auto_send_assessment": getattr(role, "auto_send_assessment", None),
        "auto_resend_assessment": getattr(role, "auto_resend_assessment", None),
        "auto_advance": getattr(role, "auto_advance", None),
        "auto_skip_assessment": bool(role.auto_skip_assessment),
        "effective_policy": effective_agent_policy(role),
    }


def _workspace_pause_state(
    db: Session,
    role: Role,
    *,
    user_id: int,
) -> dict[str, Any]:
    """Resolve the effective workspace overlay for a control response."""

    from ..services.workspace_agent_control import workspace_agent_pause_state

    return workspace_agent_pause_state(
        db,
        organization_id=int(role.organization_id),
        current_user_id=int(user_id),
    )


def _commit_audited_role_change(
    db: Session,
    role: Role,
    *,
    before: dict[str, Any],
    from_version: int,
    action: str,
    actor_user_id: int,
    reason: str = "agent chat",
) -> bool:
    """Commit a Role mutation and its versioned audit row atomically.

    Returning ``False`` for a no-op avoids duplicate version numbers/events
    when the model repeats a tool call or asks for the already-current value.
    The tool dispatcher acquires the shared Role row lock before entering these
    controls, so every successful call advances from the latest version.
    """

    try:
        changed = capture_role_change_snapshot(role) != before
        if changed:
            to_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=before,
                action=action,
                actor_user_id=int(actor_user_id),
                from_version=int(from_version),
                to_version=int(to_version),
                reason=reason,
            )
        db.commit()
        db.refresh(role)
        return changed
    except Exception:
        # Tool errors are caught by the chat engine, which continues using the
        # same Session. Clear every pending mutation so a later message commit
        # cannot separate the Role write from its audit record.
        db.rollback()
        raise


def _relock_role(db: Session, role: Role) -> Role:
    """Reacquire the shared Role lock for a post-dispatch compensation."""

    locked = (
        db.query(Role)
        .filter(
            Role.id == int(role.id),
            Role.organization_id == int(role.organization_id),
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .first()
    )
    if locked is None:  # A hard delete cannot be safely compensated.
        raise RuntimeError(f"role {role.id} disappeared during agent control")
    db.refresh(locked)
    return locked


def _relock_for_dispatch_compensation(
    db: Session,
    role: Role,
    *,
    dispatched_version: int,
) -> tuple[Role, bool]:
    """Relock and confirm the failed dispatch still owns its revision.

    Dispatch happens after the audited state commit, so another UI/chat write
    can legitimately land before a broker failure is observed. Compensation
    may only revert the exact revision that was dispatched; a later version is
    authoritative and must be preserved untouched.
    """

    locked = _relock_role(db, role)
    if int(locked.version or 1) == int(dispatched_version):
        return locked, True
    # No write on mismatch. End the FOR UPDATE transaction immediately while
    # retaining a refreshed object for the tool response.
    db.commit()
    db.refresh(locked)
    return locked, False


def _kick_cycle(role: Role, *, activation: bool = False) -> bool:
    """Enqueue the complete cohort pipeline (same as the settings UI on
    activate/resume). Never block the chat turn on a broker hiccup."""
    try:
        from ..tasks.agent_tasks import agent_cohort_tick_role

        agent_cohort_tick_role.delay(
            int(role.id),
            activation=activation,
            dispatch_role_version=int(role.version or 1),
        )
        return True
    except Exception:  # pragma: no cover — fail-closed caller handles state
        logger.exception("failed to enqueue agent cycle for role_id=%s", role.id)
        return False


def _needs_durable_task_activation(role: Role) -> bool:
    """Whether Turn on must complete a blocked task-republish workflow."""
    if bool(role.agentic_mode_enabled):
        return False
    provisioning = (
        role.assessment_task_provisioning
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    reconfiguration = provisioning.get("reconfiguration")
    return bool(
        isinstance(reconfiguration, dict)
        and str(reconfiguration.get("status") or "") == "blocked"
    )


def _queue_durable_activation(
    db: Session,
    role: Role,
    *,
    user_id: int,
) -> dict[str, Any]:
    """Persist first Turn on, then make only best-effort latency kicks.

    This path now exists only for a task-republish workflow that already needs
    durable resolution. Ordinary taskless activation explicitly skips the
    assessment stage instead.
    """
    from ..services.role_activation_intent import (
        activation_intent_task_ready,
        request_role_activation_intent,
    )

    audit_before = capture_role_change_snapshot(role)
    audit_from = int(role.version or 1)
    policy = activation_policy_values(role)
    intent = request_role_activation_intent(
        role,
        user_id=int(user_id),
        monthly_budget_cents=int(role.monthly_usd_budget_cents or 0),
        auto_promote=policy["auto_promote"],
        auto_send_assessment=policy["auto_send_assessment"],
        auto_resend_assessment=policy["auto_resend_assessment"],
        auto_advance=policy["auto_advance"],
    )
    try:
        _commit_audited_role_change(
            db,
            role,
            before=audit_before,
            from_version=audit_from,
            action="agent_activation_queued",
            actor_user_id=int(user_id),
        )
    except Exception:
        db.rollback()
        logger.exception("failed to persist chat activation role_id=%s", role.id)
        return {
            "type": "agent_state",
            "ok": False,
            "reason": "activation_persist_failed",
            "message": "I couldn't save the Turn-on request, so I left the agent off. Try again.",
            "agent": _state(role),
        }

    from ..services.workspace_agent_control import workspace_agent_pause_state

    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(role.organization_id),
        current_user_id=int(user_id),
    )
    if bool(workspace_pause["paused"]):
        return {
            "type": "agent_state",
            "ok": True,
            "action": "activation_deferred",
            "reason": "workspace_paused",
            "deferred": True,
            "pause_scope": "workspace",
            "message": (
                "Turn on is saved, but this workspace is paused. The role will "
                "finish activation after the workspace agent is resumed; no "
                "second approval click is needed."
            ),
            "activation_intent": {
                "request_id": intent.get("request_id"),
                "status": intent.get("status"),
            },
            "agent": _state(role, workspace_pause=workspace_pause),
        }

    # These dispatches reduce latency only. A broker failure is deliberately not
    # returned as a failed command because the saved outbox is retried by Beat.
    try:
        if not list(role.tasks or []):
            from ..tasks.assessment_tasks import generate_assessment_task_for_role

            generate_assessment_task_for_role.delay(
                int(role.id), int(role.organization_id)
            )
        elif activation_intent_task_ready(role):
            from ..tasks.agent_tasks import agent_cohort_tick_role

            agent_cohort_tick_role.delay(
                int(role.id),
                activation=True,
                activation_intent_id=str(intent["request_id"]),
            )
    except Exception:
        logger.warning(
            "initial chat Turn-on kick failed role_id=%s; sweep will retry",
            role.id,
            exc_info=True,
        )

    return {
        "type": "agent_state",
        "ok": True,
        "action": "activation_queued",
        "message": (
            "Turn on is saved. The agent is generating and validating its assessment, "
            "then will turn itself on after production readiness passes. No second "
            "approval click is needed, and you can leave this page."
        ),
        "activation_intent": {
            "request_id": intent.get("request_id"),
            "status": intent.get("status"),
        },
        "agent": _state(role),
    }


def set_agent_state(
    db: Session,
    role: Role,
    *,
    action: str,
    user_id: int,
) -> dict[str, Any]:
    """``activate`` (turn on / resume) or ``pause`` the role's agent.

    First activation preserves the HITL-safe action policy. Production
    activation and every resume fail closed on runtime/readiness or bootstrap
    dispatch errors.
    """
    act = (action or "").strip().lower()

    if role_is_score_only(role):
        return {
            "type": "agent_state",
            "ok": False,
            "reason": "score_only_role",
            "message": SCORE_ONLY_ROLE_AUTOMATION_MESSAGE,
            "agent": _state(role),
        }

    if act in _ACTIVATE:
        # The agent can't run uncapped — activation needs a monthly budget
        # (mirrors the settings UI). Surface a clear ask instead of failing.
        if role.monthly_usd_budget_cents is None or int(role.monthly_usd_budget_cents) <= 0:
            return {
                "type": "agent_state", "ok": False, "reason": "needs_budget",
                "message": (
                    "I can't enable the agent without a monthly spend cap — set a "
                    "monthly budget for this role first (or tell me one to set)."
                ),
                "agent": _state(role),
            }
        if _needs_durable_task_activation(role):
            return _queue_durable_activation(db, role, user_id=int(user_id))
        taskless = not any(bool(task.is_active) for task in list(role.tasks or []))
        from ..services.agent_activation_readiness import (
            activation_readiness,
            readiness_message,
        )

        readiness = activation_readiness(
            role,
            auto_skip_assessment=True if taskless else None,
        )
        if not readiness.get("ready"):
            return {
                "type": "agent_state",
                "ok": False,
                "reason": "runtime_unready",
                "message": (
                    "I left the agent off because its production runtime is not ready: "
                    + readiness_message(readiness)
                ),
                "agent": _state(role),
            }
        was_enabled = bool(role.agentic_mode_enabled)
        was_paused = role.agent_paused_at is not None
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        previous = {
            "agentic_mode_enabled": was_enabled,
            "agent_paused_at": role.agent_paused_at,
            "agent_paused_reason": role.agent_paused_reason,
            "auto_promote": bool(role.auto_promote),
            "auto_send_assessment": getattr(role, "auto_send_assessment", None),
            "auto_resend_assessment": getattr(role, "auto_resend_assessment", None),
            "auto_advance": getattr(role, "auto_advance", None),
            "starred_for_auto_sync": bool(role.starred_for_auto_sync),
            "job_status": role.job_status,
        }
        role.agentic_mode_enabled = True
        if taskless:
            role.auto_skip_assessment = True
        resumed = False
        if was_paused:
            from ..agent_runtime import budget_guard

            resumed = bool(
                budget_guard.resume_if_under_budget(
                    db, role=role, explicit=True
                )
            )
            if not resumed:
                # Do not let a chat synonym such as "start" bypass the same
                # cap/runtime guard used by the HTTP Resume controls.
                role.agentic_mode_enabled = previous["agentic_mode_enabled"]
                return {
                    "type": "agent_state",
                    "ok": False,
                    "reason": "resume_blocked",
                    "message": (
                        "I left the agent paused because its budget/runtime "
                        "resume guard did not pass."
                    ),
                    "agent": _state(role),
                }
        # Preserve concrete action-level choices on activation. A truly legacy
        # role with no choices gets the safe action-level default.
        if not was_enabled:
            policy = activation_policy_values(role)
            role.auto_promote = policy["auto_promote"]
            for field in GRANULAR_AUTOMATION_FIELDS:
                setattr(role, field, policy[field])
        if not role.starred_for_auto_sync:          # agent-on implies auto-sync
            role.starred_for_auto_sync = True
        # Chat activation has the same native-requisition go-live semantics as
        # the role PATCH. Workable remains optional for the one-switch path.
        if (
            not was_enabled
            and role.source == "requisition"
            and role.job_status == JOB_STATUS_DRAFT
            and not role.workable_job_id
        ):
            role.job_status = JOB_STATUS_OPEN
        if (not was_enabled) and not resumed:
            role.agent_bootstrap_status = "starting"
            role.agent_bootstrap_error = None
            role.agent_bootstrap_started_at = _now()
            role.agent_bootstrap_completed_at = None
        audit_action = ROLE_CHANGE_ACTION_UPDATED
        if not was_enabled:
            audit_action = ROLE_CHANGE_ACTION_AGENT_ENABLED
        elif was_paused:
            audit_action = ROLE_CHANGE_ACTION_AGENT_RESUMED
        _commit_audited_role_change(
            db,
            role,
            before=audit_before,
            from_version=audit_from,
            action=audit_action,
            actor_user_id=int(user_id),
        )
        workspace_pause = _workspace_pause_state(db, role, user_id=int(user_id))
        workspace_held = bool(workspace_pause["paused"])
        # Immutable compare-and-compensate token for this exact dispatch. A
        # newer role revision must never be overwritten if the broker rejects
        # the handoff after this commit.
        dispatched_version = int(role.version or 1)
        if (
            ((not was_enabled) or was_paused)
            and not workspace_held
        ):  # activation OR resume → kick a cycle unless the workspace holds it
            if not _kick_cycle(role, activation=not was_enabled):
                # Match the HTTP toggle's fail-closed behavior. Do not tell the
                # recruiter the agent is active when the worker queue refused
                # its bootstrap.
                role, owns_dispatched_revision = _relock_for_dispatch_compensation(
                    db,
                    role,
                    dispatched_version=dispatched_version,
                )
                if not owns_dispatched_revision:
                    return {
                        "type": "agent_state",
                        "ok": False,
                        "reason": "dispatch_failed",
                        "message": (
                            "The worker queue rejected that start, but the job "
                            "changed afterwards, so I preserved the newer settings."
                        ),
                        "compensation_skipped": True,
                        "agent": _state(role),
                    }
                compensation_before = capture_role_change_snapshot(role)
                compensation_from = dispatched_version
                role.agentic_mode_enabled = previous["agentic_mode_enabled"]
                role.agent_paused_at = previous["agent_paused_at"]
                role.agent_paused_reason = previous["agent_paused_reason"]
                role.auto_promote = previous["auto_promote"]
                for field in GRANULAR_AUTOMATION_FIELDS:
                    setattr(role, field, previous[field])
                role.starred_for_auto_sync = previous["starred_for_auto_sync"]
                role.job_status = previous["job_status"]
                role.agent_bootstrap_status = "failed"
                role.agent_bootstrap_error = "agent bootstrap dispatch failed"
                role.agent_bootstrap_completed_at = _now()
                _commit_audited_role_change(
                    db,
                    role,
                    before=compensation_before,
                    from_version=compensation_from,
                    action="agent_bootstrap_compensated",
                    actor_user_id=int(user_id),
                    reason="agent bootstrap dispatch failed",
                )
                return {
                    "type": "agent_state",
                    "ok": False,
                    "reason": "dispatch_failed",
                    "message": "The worker queue is unavailable; I left the agent off/paused. Try again.",
                    "agent": _state(role),
                }
        if workspace_held:
            result = {
                "type": "agent_state",
                "ok": True,
                "action": "activation_deferred",
                "reason": "workspace_paused",
                "deferred": True,
                "pause_scope": "workspace",
                "message": (
                    "The role's agent setting is saved, but the workspace agent "
                    "is paused. It will not run until the workspace is resumed."
                ),
                "agent": _state(role, workspace_pause=workspace_pause),
            }
        else:
            result = {
                "type": "agent_state",
                "ok": True,
                "action": "activated",
                "agent": _state(role),
            }
        # Heads-up on activation: if the role still carries OLD-engine scores,
        # surface the count so the agent OFFERS a (scoped, opt-in) re-score in
        # its reply — the recruiter steers what actually gets re-scored.
        try:
            from . import rescore as _rescore

            stale = _rescore.stale_scores_summary(db, role)
            if stale:
                result["stale_scores"] = stale
        except Exception:  # pragma: no cover — heads-up is best-effort
            logger.exception("stale-scores heads-up failed role_id=%s", role.id)
        return result

    if act in _PAUSE:
        if role.agent_paused_at is not None:
            workspace_pause = _workspace_pause_state(
                db, role, user_id=int(user_id)
            )
            return {
                "type": "agent_state",
                "ok": True,
                "action": "paused",
                "agent": _state(role, workspace_pause=workspace_pause),
            }
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        role.agent_paused_at = _now()
        role.agent_paused_reason = "paused by recruiter"
        _commit_audited_role_change(
            db,
            role,
            before=audit_before,
            from_version=audit_from,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
            actor_user_id=int(user_id),
            reason="paused by recruiter via agent chat",
        )
        workspace_pause = _workspace_pause_state(db, role, user_id=int(user_id))
        return {
            "type": "agent_state",
            "ok": True,
            "action": "paused",
            "agent": _state(role, workspace_pause=workspace_pause),
        }

    return {
        "type": "agent_state", "ok": False, "reason": "unknown_action",
        "message": f"I didn't recognise '{action}' — say 'activate' or 'pause'.",
        "agent": _state(role),
    }


def adjust_agent_settings(
    db: Session, role: Role, *,
    user_id: int,
    monthly_budget_cents: int | None = None,
    auto_reject: bool | None = None,
    auto_reject_pre_screen: bool | None = None,
    auto_promote: bool | None = None,
    auto_send_assessment: bool | None = None,
    auto_resend_assessment: bool | None = None,
    auto_advance: bool | None = None,
    auto_skip_assessment: bool | None = None,
) -> dict[str, Any]:
    """Update budget / auto-reject / auto-promote / auto-skip-assessment.

    Only passed fields change. A healthier budget may resume an automatic hold
    (same helper as the settings UI); it never clears a recruiter-authored
    manual pause. ``auto_reject_pre_screen`` governs the cheap screening gate;
    ``auto_reject`` separately governs deterministic full CV/role-fit rejects.
    Assessment-stage and LLM-authored rejects remain human-confirmed.
    """
    if role_is_score_only(role):
        return {
            "type": "agent_settings",
            "ok": False,
            "reason": "score_only_role",
            "message": SCORE_ONLY_ROLE_AUTOMATION_MESSAGE,
            "changed": [],
            "resumed": False,
            "resume_error": None,
            "agent": _state(role),
        }

    if (
        auto_skip_assessment is False
        and not any(bool(task.is_active) for task in (role.tasks or []))
    ):
        return {
            "type": "agent_settings",
            "ok": False,
            "reason": "assessment_task_required",
            "message": (
                "Choose an active assessment task before turning assessment "
                "skipping off for this role."
            ),
            "changed": [],
            "resumed": False,
            "resume_error": None,
            "agent": _state(role),
        }

    audit_before = capture_role_change_snapshot(role)
    audit_from = int(role.version or 1)
    changed: list[str] = []
    if monthly_budget_cents is not None:
        if int(monthly_budget_cents) <= 0:
            return {
                "type": "agent_settings",
                "ok": False,
                "reason": "invalid_budget",
                "message": "The monthly spend cap must be greater than zero.",
                "changed": [],
                "resumed": False,
                "resume_error": None,
                "agent": _state(role),
            }
        role.monthly_usd_budget_cents = int(monthly_budget_cents)
        changed.append("monthly_budget")
    if auto_reject is not None:
        role.auto_reject = bool(auto_reject)
        changed.append("auto_reject")
    if auto_reject_pre_screen is not None:
        role.auto_reject_pre_screen = bool(auto_reject_pre_screen)
        changed.append("auto_reject_pre_screen")
    if auto_promote is not None:
        role.auto_promote = bool(auto_promote)
        changed.append("auto_promote")
        concrete_values = {
            bool(getattr(role, field))
            for field in GRANULAR_AUTOMATION_FIELDS
            if getattr(role, field, None) is not None
        }
        explicit_granular = any(
            value is not None
            for value in (
                auto_send_assessment,
                auto_resend_assessment,
                auto_advance,
            )
        )
        if not explicit_granular and len(concrete_values) <= 1:
            for field in GRANULAR_AUTOMATION_FIELDS:
                setattr(role, field, bool(auto_promote))
    for field, value in (
        ("auto_send_assessment", auto_send_assessment),
        ("auto_resend_assessment", auto_resend_assessment),
        ("auto_advance", auto_advance),
    ):
        if value is not None:
            setattr(role, field, bool(value))
            changed.append(field)
    if any(
        getattr(role, field, None) is not None
        for field in GRANULAR_AUTOMATION_FIELDS
    ):
        role.auto_promote = all(
            role_automation_enabled(role, field)
            for field in GRANULAR_AUTOMATION_FIELDS
        )
    skip_changed = (
        auto_skip_assessment is not None
        and bool(role.auto_skip_assessment) != bool(auto_skip_assessment)
    )
    if auto_skip_assessment is not None:
        role.auto_skip_assessment = bool(auto_skip_assessment)
        changed.append("auto_skip_assessment")

    if changed:
        from ..services.role_activation_intent import (
            refresh_role_activation_intent_policy,
        )

        refresh_role_activation_intent_policy(role)

    resumed = False
    if monthly_budget_cents is not None:
        try:
            from ..agent_runtime import budget_guard

            resumed = bool(
                budget_guard.resume_if_under_budget(
                    db, role=role, explicit=False
                )
            )
        except Exception:  # pragma: no cover — never block the turn
            logger.exception("resume_if_under_budget failed for role_id=%s", role.id)

    _commit_audited_role_change(
        db,
        role,
        before=audit_before,
        from_version=audit_from,
        action=(
            ROLE_CHANGE_ACTION_AGENT_RESUMED
            if resumed
            else ROLE_CHANGE_ACTION_UPDATED
        ),
        actor_user_id=int(user_id),
    )
    # The budget-resume dispatch owns only this committed revision. Keep this
    # token stable across follow-up reconciliation and broker I/O.
    dispatched_version = int(role.version or 1)
    workspace_pause = _workspace_pause_state(db, role, user_id=int(user_id))
    workspace_held = bool(workspace_pause["paused"])
    # An assessment-stage flip re-flows already-pending send/advance cards
    # right away — same reconcile the settings-UI PATCH runs (Codex #866).
    if skip_changed:
        try:
            from ..services.bulk_decision_service import (
                reconcile_pending_positive_decisions,
            )

            reconcile_pending_positive_decisions(db, role=role)
            db.commit()
        except Exception:  # pragma: no cover — never block the turn
            logger.exception(
                "assessment-stage reconcile failed for role_id=%s", role.id
            )
    resume_error = None
    compensation_skipped = False
    if resumed and not workspace_held and not _kick_cycle(role):
        # The shared readiness gate proved the runtime healthy immediately
        # before resume, but the broker can still reject this specific handoff.
        # Restore the pause and persist a durable failed acknowledgement rather
        # than returning a green agent that never received its bootstrap task.
        role, owns_dispatched_revision = _relock_for_dispatch_compensation(
            db,
            role,
            dispatched_version=dispatched_version,
        )
        if owns_dispatched_revision:
            compensation_before = capture_role_change_snapshot(role)
            role.agent_paused_at = _now()
            role.agent_paused_reason = "agent bootstrap dispatch failed"
            role.agent_bootstrap_status = "failed"
            role.agent_bootstrap_error = "agent bootstrap dispatch failed"
            role.agent_bootstrap_completed_at = _now()
            _commit_audited_role_change(
                db,
                role,
                before=compensation_before,
                from_version=dispatched_version,
                action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
                actor_user_id=int(user_id),
                reason="agent bootstrap dispatch failed",
            )
            resume_error = "worker queue unavailable; agent left paused"
        else:
            compensation_skipped = True
            resume_error = (
                "worker queue unavailable; newer job settings were preserved"
            )
        resumed = False
    # Refresh after any dispatch compensation so the response always describes
    # effective authority (workspace overlay first, then the role's local hold).
    workspace_pause = _workspace_pause_state(db, role, user_id=int(user_id))
    workspace_held = bool(workspace_pause["paused"])
    result = {
        "type": "agent_settings", "ok": True, "changed": changed,
        "resumed": resumed,
        "resume_error": resume_error,
        "agent": _state(role, workspace_pause=workspace_pause),
    }
    if resumed and workspace_held:
        result.update(
            {
                "deferred": True,
                "pause_scope": "workspace",
                "message": (
                    "The role's local budget hold is cleared, but the workspace "
                    "agent is paused. It will not run until the workspace is resumed."
                ),
            }
        )
    if compensation_skipped:
        result["compensation_skipped"] = True
    return result


def sync_workable_comments(db: Session, role: Role, *, user: Any = None) -> dict[str, Any]:
    """Force an immediate Workable sync for THIS role so its candidates' recruiter
    comments / ratings (and stages) refresh now, instead of waiting for the next
    scheduled sweep. Reuses the existing ``kick_off_filtered_sync`` (same path the
    star-role flow uses) — full mode, scoped to this one job. Asynchronous: the
    fresh comments land as the run completes (seconds, rate-limited)."""
    from ..models.organization import Organization

    org = db.query(Organization).filter(Organization.id == role.organization_id).first()
    shortcode = (role.workable_job_id or "").strip() or None
    if not shortcode and isinstance(role.workable_job_data, dict):
        shortcode = (str(role.workable_job_data.get("shortcode") or "").strip()) or None
    if org is None or not shortcode:
        return {
            "type": "workable_sync", "ok": False, "reason": "not_workable",
            "message": (
                "This role isn't synced from Workable, so there are no Workable "
                "comments to refresh."
            ),
        }

    try:
        # Lazy import — the sync route module pulls heavy Workable deps.
        from ..domains.workable_sync.routes import kick_off_filtered_sync

        run_id = kick_off_filtered_sync(
            db, org=org, job_shortcodes=[shortcode],
            requested_by_user_id=int(user.id) if user is not None else None,
            mode="full",
        )
    except Exception:  # pragma: no cover — never sink the chat turn on a sync hiccup
        logger.exception("sync_workable_comments failed for role_id=%s", role.id)
        return {
            "type": "workable_sync", "ok": False, "reason": "error",
            "message": "I couldn't start the Workable sync just now — try again in a moment.",
        }

    if run_id is None:
        return {
            "type": "workable_sync", "ok": True, "status": "already_running",
            "message": (
                "A Workable sync is already in progress — the latest recruiter "
                "comments will land shortly."
            ),
        }
    return {
        "type": "workable_sync", "ok": True, "status": "started", "run_id": run_id,
        "message": (
            "Started a fresh Workable sync for this role — recruiter comments "
            "refresh in a moment; ask me again shortly and I'll re-read them."
        ),
    }


__all__ = ["set_agent_state", "adjust_agent_settings", "sync_workable_comments"]
