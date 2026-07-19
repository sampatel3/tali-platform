"""Fair, readiness-aware selection for durable role-activation recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import Session, selectinload

from ..models.organization import Organization
from ..models.role import Role, role_tasks
from ..models.task import Task
from .role_activation_intent import (
    ACTIVATION_ACTIVE_STATUSES,
    activation_intent_state,
    activation_intent_task_ready,
    block_activation_intent_if_task_exhausted,
)


@dataclass(frozen=True)
class ActivationRecoveryBatch:
    keys: tuple[tuple[int, str], ...]
    blocked: int
    scanned: int


def _dispatch_provisioning(role: Role, *, now: datetime) -> dict:
    provisioning = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    intent = activation_intent_state(role)
    intent["last_sweep_dispatched_at"] = now.astimezone(timezone.utc).isoformat()
    provisioning["activation_intent"] = intent
    return provisioning


def _claim_sweep_dispatch(
    db: Session,
    *,
    role: Role,
    request_id: str,
    now: datetime,
) -> bool:
    """CAS-claim a dispatch; row locks make this skip rather than wait on PG."""

    if str(activation_intent_state(role).get("request_id") or "") != str(
        request_id
    ):
        return False
    updated_at = getattr(role, "updated_at", None)
    query = db.query(Role).filter(Role.id == int(role.id))
    if updated_at is None:
        query = query.filter(Role.updated_at.is_(None))
    elif db.get_bind().dialect.name == "sqlite":
        expected = updated_at.strftime("%Y-%m-%d %H:%M:%S")
        if updated_at.microsecond:
            expected += f".{updated_at.microsecond:06d}"
        query = query.filter(cast(Role.updated_at, String) == expected)
    else:
        query = query.filter(Role.updated_at == updated_at)
    claimed = query.update(
        {
            Role.assessment_task_provisioning: _dispatch_provisioning(
                role,
                now=now,
            ),
            Role.updated_at: now,
        },
        synchronize_session=False,
    )
    return claimed == 1


def select_activation_recovery_batch(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
) -> ActivationRecoveryBatch:
    """Select up to ``limit`` ready intents without pre-limit starvation.

    SQL first removes taskless waiting intents. The remaining small actionable
    set is paged and checked with the canonical Python state machine before the
    dispatch limit is applied. A durable dispatch timestamp provides round-
    robin fairness when a broker accepted job remains pending across sweeps.
    """

    bounded_limit = max(0, min(int(limit), 1000))
    if bounded_limit == 0:
        return ActivationRecoveryBatch(keys=(), blocked=0, scanned=0)
    current_time = now or datetime.now(timezone.utc)
    activation_status = Role.assessment_task_provisioning["activation_intent"][
        "status"
    ].as_string()
    last_dispatched = Role.assessment_task_provisioning["activation_intent"][
        "last_sweep_dispatched_at"
    ].as_string()
    next_attempt = Role.assessment_task_provisioning["activation_intent"][
        "next_attempt_at"
    ].as_string()
    canonical_timestamp = next_attempt.like("____-__-__T__:__:__%")
    due_status = or_(
        activation_status == "pending",
        and_(
            activation_status == "retry_wait",
            or_(
                next_attempt.is_(None),
                next_attempt <= current_time.isoformat(),
                ~canonical_timestamp,
            ),
        ),
    )
    actionable_task = (
        db.query(role_tasks.c.role_id)
        .join(Task, Task.id == role_tasks.c.task_id)
        .filter(
            role_tasks.c.role_id == Role.id,
            or_(
                Task.is_active.is_(True),
                Task.extra_data["battle_test"]["verdict"].as_string() == "pass",
                Task.extra_data["battle_test_provisioning"][
                    "status"
                ].as_string()
                == "repair_exhausted",
            ),
        )
        .exists()
    )
    base_query = (
        db.query(Role)
        .options(selectinload(Role.tasks))
        .join(Organization, Organization.id == Role.organization_id)
        .filter(
            Role.deleted_at.is_(None),
            Organization.agent_workspace_paused_at.is_(None),
            Role.agentic_mode_enabled.is_(False),
            Role.assessment_task_provisioning.isnot(None),
            activation_status.in_(sorted(ACTIVATION_ACTIVE_STATUSES)),
            due_status,
            or_(Role.auto_skip_assessment.is_(True), actionable_task),
        )
        .order_by(
            last_dispatched.asc().nullsfirst(),
            Role.updated_at.asc().nullsfirst(),
            Role.id.asc(),
        )
    )

    page_size = max(100, min(bounded_limit * 2, 1000))
    offset = 0
    scanned = 0
    blocked = 0
    candidates: list[tuple[Role, str]] = []
    # Keep the database ordering stable while operational JSON markers are
    # accumulated. One final commit both persists fairness and terminal blocks.
    with db.no_autoflush:
        while len(candidates) < bounded_limit:
            page = (
                base_query.offset(offset)
                .limit(page_size)
                .with_for_update(of=Role, skip_locked=True)
                .populate_existing()
                .all()
            )
            if not page:
                break
            offset += len(page)
            scanned += len(page)
            for role in page:
                if block_activation_intent_if_task_exhausted(
                    role,
                    now=current_time,
                ):
                    blocked += 1
                    continue
                intent = activation_intent_state(role)
                request_id = str(intent.get("request_id") or "")
                if not request_id or not activation_intent_task_ready(role):
                    continue
                candidates.append((role, request_id))
                if len(candidates) >= bounded_limit:
                    break
            if len(page) < page_size:
                break

    keys = [
        (int(role.id), request_id)
        for role, request_id in candidates
        if _claim_sweep_dispatch(
            db,
            role=role,
            request_id=request_id,
            now=current_time,
        )
    ]
    if scanned or blocked or candidates:
        db.commit()
    return ActivationRecoveryBatch(
        keys=tuple(keys),
        blocked=blocked,
        scanned=scanned,
    )


__all__ = ["ActivationRecoveryBatch", "select_activation_recovery_batch"]
