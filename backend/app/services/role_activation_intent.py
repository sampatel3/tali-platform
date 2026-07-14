"""Durable one-click activation for requisition-backed role agents.

The browser records an authorization; it is never the workflow engine.  The
authorization lives inside ``Role.assessment_task_provisioning`` so the same
outbox that recovers task generation can carry activation through generation,
battle testing, repository verification, production readiness, and the first
cohort worker acknowledgement.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role


ACTIVATION_PENDING = "pending"
ACTIVATION_RETRY_WAIT = "retry_wait"
ACTIVATION_BLOCKED = "blocked"
ACTIVATION_SUCCEEDED = "succeeded"
ACTIVATION_CANCELLED = "cancelled"
ACTIVATION_ACTIVE_STATUSES = frozenset(
    {ACTIVATION_PENDING, ACTIVATION_RETRY_WAIT}
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def activation_intent_state(role: Role) -> dict[str, Any]:
    provisioning = getattr(role, "assessment_task_provisioning", None)
    if not isinstance(provisioning, dict):
        return {}
    intent = provisioning.get("activation_intent")
    return dict(intent) if isinstance(intent, dict) else {}


def _write_intent(role: Role, intent: dict[str, Any]) -> None:
    provisioning = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    provisioning["activation_intent"] = intent
    role.assessment_task_provisioning = provisioning


def request_role_activation_intent(
    role: Role,
    *,
    user_id: int,
    monthly_budget_cents: int,
    auto_promote: bool = True,
    auto_send_assessment: bool | None = None,
    auto_resend_assessment: bool | None = None,
    auto_advance: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist (or idempotently refresh) the recruiter's Turn-on command."""
    current_time = now or _utcnow()
    budget = int(monthly_budget_cents)
    if budget <= 0:
        raise ValueError("monthly_budget_cents must be greater than zero")

    from .task_provisioning_service import (
        authorize_assessment_task_provisioning,
        task_provisioning_state,
    )

    try:
        linked = list(getattr(role, "tasks", None) or [])
    except Exception:
        linked = []
    if not linked:
        authorize_assessment_task_provisioning(
            role, reason="agent_turn_on", now=current_time
        )

    provisioning = task_provisioning_state(role)
    exact_task_id = None
    task_selection_error = None
    if len(linked) == 1:
        task = linked[0]
        extra = task.extra_data if isinstance(task.extra_data, dict) else {}
        generated_review_draft = bool(
            not bool(task.is_active)
            and extra.get("generated")
            and extra.get("needs_review", True)
        )
        if bool(task.is_active) or generated_review_draft:
            # A preserved/manual active task needs no automatic content
            # approval. In a republish-blocked state, pressing Turn on again is
            # the explicit human confirmation that it remains the intended
            # choice. A generated draft follows battle-test → auto-approval.
            exact_task_id = int(task.id)
        else:
            task_selection_error = (
                "The linked assessment task is inactive and cannot be approved "
                "automatically because it is not a generated review draft. "
                "Approve or replace the task, then press Turn on again."
            )
    elif len(linked) > 1:
        task_selection_error = (
            "Turn on cannot choose safely between multiple linked assessment "
            "tasks. Keep one intended task (or configure the task experiment) "
            "and press Turn on again."
        )

    existing = activation_intent_state(role)
    if str(existing.get("status") or "") in ACTIVATION_ACTIVE_STATUSES:
        request_id = str(existing.get("request_id") or uuid.uuid4().hex)
        requested_at = str(existing.get("requested_at") or _iso(current_time))
        attempts = int(existing.get("attempts") or 0)
    else:
        request_id = uuid.uuid4().hex
        requested_at = _iso(current_time)
        attempts = 0
    send_enabled = (
        bool(auto_promote)
        if auto_send_assessment is None
        else bool(auto_send_assessment)
    )
    resend_enabled = (
        bool(auto_promote)
        if auto_resend_assessment is None
        else bool(auto_resend_assessment)
    )
    advance_enabled = (
        bool(auto_promote) if auto_advance is None else bool(auto_advance)
    )
    intent = {
        **existing,
        "command": "approve_when_ready",
        "status": (
            ACTIVATION_BLOCKED if task_selection_error else ACTIVATION_PENDING
        ),
        "request_id": request_id,
        "provisioning_request_id": provisioning.get("request_id"),
        "task_id": exact_task_id,
        "monthly_usd_budget_cents": budget,
        "auto_promote": bool(auto_promote),
        "auto_send_assessment": send_enabled,
        "auto_resend_assessment": resend_enabled,
        "auto_advance": advance_enabled,
        "requested_by_user_id": int(user_id),
        "requested_at": requested_at,
        "last_requested_at": _iso(current_time),
        "updated_at": _iso(current_time),
        "attempts": attempts,
        "last_error": task_selection_error,
        "next_attempt_at": None,
        "cancelled_at": None,
        "completed_at": None,
    }
    if task_selection_error:
        intent["blocked_at"] = _iso(current_time)
    else:
        intent["blocked_at"] = None
    _write_intent(role, intent)
    provisioning = dict(role.assessment_task_provisioning or {})
    reconfiguration = provisioning.get("reconfiguration")
    if (
        not task_selection_error
        and isinstance(reconfiguration, dict)
        and str(reconfiguration.get("status") or "") == "blocked"
    ):
        provisioning["reconfiguration"] = {
            **reconfiguration,
            "status": "pending",
            "resolution": "preserved_task_confirmed_by_user",
            "confirmed_task_id": exact_task_id,
            "confirmed_by_user_id": int(user_id),
            "last_error": None,
            "updated_at": _iso(current_time),
        }
        role.assessment_task_provisioning = provisioning
    # Persist the authorized cap and policy immediately while leaving runtime
    # power state untouched until a worker receives the activation task.
    role.monthly_usd_budget_cents = budget
    # Policy choices are harmless while the role is off and should read back
    # immediately even when task generation/worker activation is still pending.
    role.auto_promote = bool(auto_promote)
    role.auto_send_assessment = send_enabled
    role.auto_resend_assessment = resend_enabled
    role.auto_advance = advance_enabled
    return intent


def cancel_role_activation_intent(
    role: Role,
    *,
    user_id: int | None,
    reason: str,
    now: datetime | None = None,
) -> bool:
    intent = activation_intent_state(role)
    if str(intent.get("status") or "") not in ACTIVATION_ACTIVE_STATUSES:
        return False
    current_time = now or _utcnow()
    intent.update(
        {
            "status": ACTIVATION_CANCELLED,
            "cancelled_at": _iso(current_time),
            "cancelled_by_user_id": int(user_id) if user_id is not None else None,
            "cancel_reason": str(reason or "activation cancelled")[:500],
            "updated_at": _iso(current_time),
            "next_attempt_at": None,
        }
    )
    _write_intent(role, intent)
    return True


def activation_intent_is_due(
    role: Role, *, now: datetime | None = None
) -> bool:
    intent = activation_intent_state(role)
    status = str(intent.get("status") or "")
    if status == ACTIVATION_PENDING:
        return True
    if status != ACTIVATION_RETRY_WAIT:
        return False
    next_attempt_at = _parse_time(intent.get("next_attempt_at"))
    return next_attempt_at is None or next_attempt_at <= (now or _utcnow())


def _intent_task(role: Role, intent: dict[str, Any]):
    requested_task_id = intent.get("task_id")
    provisioning = (
        role.assessment_task_provisioning
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    if requested_task_id is None:
        requested_task_id = provisioning.get("task_id")
    drafts = []
    eligible = []
    for task in list(getattr(role, "tasks", None) or []):
        extra = task.extra_data if isinstance(task.extra_data, dict) else {}
        if bool(task.is_active):
            eligible.append(task)
        if (
            not bool(task.is_active)
            and extra.get("generated")
            and extra.get("needs_review", True)
        ):
            drafts.append(task)
            eligible.append(task)
    if requested_task_id is not None:
        return next(
            (task for task in eligible if int(task.id) == int(requested_task_id)),
            None,
        )
    if len(drafts) == 1:
        return drafts[0]
    active = [task for task in eligible if bool(task.is_active)]
    return active[0] if len(active) == 1 else None


def activation_intent_task_ready(role: Role) -> bool:
    if not activation_intent_is_due(role):
        return False
    intent = activation_intent_state(role)
    task = _intent_task(role, intent)
    if task is None:
        return False
    if bool(task.is_active):
        return True
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    return (extra.get("battle_test") or {}).get("verdict") == "pass"


def block_activation_intent_if_task_exhausted(
    role: Role, *, now: datetime | None = None
) -> bool:
    """Surface the bounded auto-repair terminal state without worker dispatch."""
    intent = activation_intent_state(role)
    if str(intent.get("status") or "") not in ACTIVATION_ACTIVE_STATUSES:
        return False
    task = _intent_task(role, intent)
    if task is None:
        return False
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    battle_state = extra.get("battle_test_provisioning") or {}
    if str(battle_state.get("status") or "") != "repair_exhausted":
        return False
    current_time = now or _utcnow()
    intent.update(
        {
            "status": ACTIVATION_BLOCKED,
            "task_id": int(task.id),
            "last_error": (
                "Automated assessment repair was exhausted. Update the job "
                "specification and press Turn on again, or explicitly skip "
                "the assessment stage."
            ),
            "next_attempt_at": None,
            "blocked_at": _iso(current_time),
            "updated_at": _iso(current_time),
        }
    )
    _write_intent(role, intent)
    return True


def _record_retry(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    error: str,
    now: datetime,
    blocked: bool = False,
) -> dict[str, Any]:
    db.rollback()
    role = (
        db.query(Role)
        .filter(Role.id == int(role_id))
        .with_for_update()
        .one_or_none()
    )
    if role is None:
        return {"status": "missing"}
    intent = activation_intent_state(role)
    if str(intent.get("request_id") or "") != str(request_id):
        db.rollback()
        return {"status": "superseded"}
    if str(intent.get("status") or "") not in ACTIVATION_ACTIVE_STATUSES:
        db.rollback()
        return {"status": str(intent.get("status") or "inactive")}
    status = ACTIVATION_BLOCKED if blocked else ACTIVATION_RETRY_WAIT
    intent.update(
        {
            "status": status,
            "attempts": int(intent.get("attempts") or 0) + 1,
            "last_error": str(error or "activation failed")[:2000],
            "next_attempt_at": (
                None if blocked else _iso(now + timedelta(minutes=5))
            ),
            "updated_at": _iso(now),
        }
    )
    _write_intent(role, intent)
    db.commit()
    return {"status": status, "reason": intent["last_error"]}


def complete_role_activation_intent(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    worker_task_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Approve the exact passing task and atomically switch the role ON.

    This runs *inside* ``agent_cohort_tick_role``. Therefore the role cannot be
    reported ON unless the queue accepted the very worker that will bootstrap
    it. Readiness/repository failures roll the transaction back and become a
    cooled-down durable retry; duplicates collapse on the row lock and worker
    task id.
    """
    current_time = now or _utcnow()
    role = (
        db.query(Role)
        .filter(Role.id == int(role_id), Role.deleted_at.is_(None))
        .with_for_update()
        .one_or_none()
    )
    if role is None:
        return {"status": "missing"}
    intent = activation_intent_state(role)
    if str(intent.get("request_id") or "") != str(request_id or ""):
        return {"status": "superseded"}
    status = str(intent.get("status") or "")
    if status == ACTIVATION_SUCCEEDED:
        same_worker = bool(
            worker_task_id
            and str(intent.get("activation_worker_task_id") or "")
            == str(worker_task_id)
        )
        return {
            "status": "already_activated" if same_worker else "duplicate",
            "role_id": int(role.id),
        }
    if status not in ACTIVATION_ACTIVE_STATUSES:
        return {"status": status or "inactive"}
    if not activation_intent_is_due(role, now=current_time):
        return {"status": "not_due"}
    if bool(role.agentic_mode_enabled):
        return _record_retry(
            db,
            role_id=int(role.id),
            request_id=request_id,
            error="role was enabled by a different activation command",
            now=current_time,
            blocked=True,
        )

    task = _intent_task(role, intent)
    if task is None:
        # Generation/battle provisioning owns the next wake. This is not an
        # error and should not introduce a timer that competes with its sweep.
        return {"status": "waiting_for_task"}
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    battle = extra.get("battle_test") if isinstance(extra.get("battle_test"), dict) else {}
    # An already-active task was previously approved by a human. In the
    # republish HITL path, the new Turn-on command explicitly confirms that
    # preserved choice; only generated inactive drafts require a battle pass
    # before automatic approval.
    if not bool(task.is_active) and battle.get("verdict") != "pass":
        battle_state = extra.get("battle_test_provisioning") or {}
        if str(battle_state.get("status") or "") == "repair_exhausted":
            return _record_retry(
                db,
                role_id=int(role.id),
                request_id=request_id,
                error="automated assessment repair was exhausted",
                now=current_time,
                blocked=True,
            )
        return {"status": "waiting_for_battle_test"}

    try:
        from .agent_activation_readiness import activation_readiness, readiness_message
        from .task_approval_service import approve_task_for_use

        if not bool(task.is_active):
            approve_task_for_use(
                db,
                task,
                user_id=(
                    int(intent["requested_by_user_id"])
                    if intent.get("requested_by_user_id") is not None
                    else None
                ),
            )
        role.monthly_usd_budget_cents = int(intent["monthly_usd_budget_cents"])
        role.auto_skip_assessment = False
        readiness = activation_readiness(role, auto_skip_assessment=False)
        if not readiness.get("ready"):
            return _record_retry(
                db,
                role_id=int(role.id),
                request_id=request_id,
                error=readiness_message(readiness),
                now=current_time,
            )

        role.agentic_mode_enabled = True
        role.agent_paused_at = None
        role.agent_paused_reason = None
        role.auto_promote = bool(intent.get("auto_promote", True))
        role.auto_send_assessment = bool(
            intent.get("auto_send_assessment", intent.get("auto_promote", True))
        )
        role.auto_resend_assessment = bool(
            intent.get("auto_resend_assessment", intent.get("auto_promote", True))
        )
        role.auto_advance = bool(
            intent.get("auto_advance", intent.get("auto_promote", True))
        )
        role.starred_for_auto_sync = True
        if (
            role.source == "requisition"
            and role.job_status == JOB_STATUS_DRAFT
        ):
            role.job_status = JOB_STATUS_OPEN
        role.agent_bootstrap_status = "starting"
        role.agent_bootstrap_error = None
        role.agent_bootstrap_started_at = current_time
        role.agent_bootstrap_completed_at = None
        intent.update(
            {
                "status": ACTIVATION_SUCCEEDED,
                "task_id": int(task.id),
                "attempts": int(intent.get("attempts") or 0) + 1,
                "last_error": None,
                "next_attempt_at": None,
                "activation_worker_task_id": worker_task_id,
                "activated_at": _iso(current_time),
                "completed_at": _iso(current_time),
                "updated_at": _iso(current_time),
            }
        )
        _write_intent(role, intent)
        provisioning = dict(role.assessment_task_provisioning or {})
        reconfiguration = provisioning.get("reconfiguration")
        if isinstance(reconfiguration, dict) and str(
            reconfiguration.get("status") or ""
        ) in {"pending", "running"}:
            provisioning["reconfiguration"] = {
                **reconfiguration,
                "status": "succeeded",
                "replacement_task_id": int(task.id),
                "last_error": None,
                "completed_at": _iso(current_time),
                "updated_at": _iso(current_time),
            }
        provisioning["interview_focus_provisioning"] = {
            "status": "succeeded" if bool(role.interview_focus) else "pending",
            "last_error": None,
            "next_attempt_at": None,
            "updated_at": _iso(current_time),
        }
        provisioning["tech_questions_provisioning"] = {
            "status": "succeeded" if bool(role.tech_questions_signature) else "pending",
            "last_error": None,
            "next_attempt_at": None,
            "updated_at": _iso(current_time),
        }
        role.assessment_task_provisioning = provisioning
        db.add(role)
        db.commit()
    except Exception as exc:
        return _record_retry(
            db,
            role_id=int(role.id),
            request_id=request_id,
            error=f"{type(exc).__name__}: {exc}",
            now=current_time,
        )

    # Checklist + interview-focus are downstream durable conveniences. Their
    # own recovery conditions remain persisted; neither can falsify the ON
    # transition after the atomic activation commit.
    try:
        from .agent_activation_checklist import surface_activation_questions

        surface_activation_questions(db, role=role)
        db.commit()
    except Exception:
        db.rollback()
    try:
        from .application_events import on_role_jd_attached

        on_role_jd_attached(role)
        from ..tasks.automation_tasks import regenerate_role_tech_questions

        regenerate_role_tech_questions.delay(int(role.id))
    except Exception:
        pass
    return {"status": "activated", "role_id": int(role.id), "task_id": int(task.id)}


__all__ = [
    "ACTIVATION_ACTIVE_STATUSES",
    "ACTIVATION_BLOCKED",
    "ACTIVATION_CANCELLED",
    "ACTIVATION_PENDING",
    "ACTIVATION_RETRY_WAIT",
    "ACTIVATION_SUCCEEDED",
    "activation_intent_is_due",
    "activation_intent_state",
    "activation_intent_task_ready",
    "block_activation_intent_if_task_exhausted",
    "cancel_role_activation_intent",
    "complete_role_activation_intent",
    "request_role_activation_intent",
]
