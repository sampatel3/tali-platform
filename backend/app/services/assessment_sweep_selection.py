"""Readiness-aware, claimed selection for assessment recovery outboxes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TypeVar

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import Query, Session

from ..models.organization import Organization
from ..models.role import Role
from ..models.task import Task
from .task_battle_test import (
    BATTLE_TEST_FAILED,
    BATTLE_TEST_PENDING,
    BATTLE_TEST_REPAIR_FAILED,
    BATTLE_TEST_REPAIR_PENDING,
    BATTLE_TEST_REPAIR_RETRY_WAIT,
    BATTLE_TEST_REPAIRING,
    BATTLE_TEST_RETRY_WAIT,
    BATTLE_TEST_RUNNING,
    BATTLE_TEST_STALE_AFTER,
    battle_test_provisioning_action,
)
from .task_provisioning_state import (
    PROVISIONING_FAILED,
    PROVISIONING_PENDING,
    PROVISIONING_RECOVERABLE_STATUSES,
    PROVISIONING_RETRY_WAIT,
    PROVISIONING_RUNNING,
    PROVISIONING_STALE_AFTER,
    provisioning_state_is_due,
    task_provisioning_state,
)


@dataclass(frozen=True)
class RecoverySelection:
    keys: tuple
    scanned: int


T = TypeVar("T")


def _due_or_malformed(expression, *, now: datetime):
    canonical = expression.like("____-__-__T__:__:__%")
    return or_(expression.is_(None), expression <= now.isoformat(), ~canonical)


def _stale_or_malformed(expression, *, before: datetime):
    canonical = expression.like("____-__-__T__:__:__%")
    return or_(expression.is_(None), expression <= before.isoformat(), ~canonical)


def _collect_ready(
    query: Query,
    *,
    model,
    limit: int,
    ready: Callable[[T], object | None],
) -> tuple[list[tuple[T, object]], int]:
    bounded_limit = max(0, min(int(limit), 1000))
    if bounded_limit == 0:
        return [], 0
    page_size = max(100, min(bounded_limit * 2, 1000))
    offset = 0
    scanned = 0
    candidates: list[tuple[T, object]] = []
    while len(candidates) < bounded_limit:
        page = (
            query.offset(offset)
            .limit(page_size)
            .with_for_update(of=model, skip_locked=True)
            .populate_existing()
            .all()
        )
        if not page:
            break
        offset += len(page)
        scanned += len(page)
        for row in page:
            key = ready(row)
            if key is not None:
                candidates.append((row, key))
                if len(candidates) >= bounded_limit:
                    break
        if len(page) < page_size:
            break
    return candidates, scanned


def _updated_at_filter(db: Session, query: Query, row) -> Query:
    updated_at = getattr(row, "updated_at", None)
    column = type(row).updated_at
    if updated_at is None:
        return query.filter(column.is_(None))
    if db.get_bind().dialect.name == "sqlite":
        expected = updated_at.strftime("%Y-%m-%d %H:%M:%S")
        if updated_at.microsecond:
            expected += f".{updated_at.microsecond:06d}"
        return query.filter(cast(column, String) == expected)
    return query.filter(column == updated_at)


def _claim_role_payload(
    db: Session,
    *,
    role: Role,
    payload: dict,
    now: datetime,
) -> bool:
    query = _updated_at_filter(
        db,
        db.query(Role).filter(Role.id == int(role.id)),
        role,
    )
    return (
        query.update(
            {
                Role.assessment_task_provisioning: payload,
                Role.updated_at: now,
            },
            synchronize_session=False,
        )
        == 1
    )


def _claim_task_extra(
    db: Session,
    *,
    task: Task,
    extra: dict,
    now: datetime,
) -> bool:
    query = _updated_at_filter(
        db,
        db.query(Task).filter(Task.id == int(task.id)),
        task,
    )
    return (
        query.update(
            {Task.extra_data: extra, Task.updated_at: now},
            synchronize_session=False,
        )
        == 1
    )


def select_generation_recovery_batch(
    db: Session,
    *,
    limit: int,
    now: datetime,
) -> RecoverySelection:
    status = Role.assessment_task_provisioning["status"].as_string()
    next_attempt = Role.assessment_task_provisioning["next_attempt_at"].as_string()
    updated = Role.assessment_task_provisioning["updated_at"].as_string()
    marker = Role.assessment_task_provisioning[
        "last_sweep_dispatched_at"
    ].as_string()
    due = or_(
        status == PROVISIONING_PENDING,
        and_(
            status == PROVISIONING_RUNNING,
            _stale_or_malformed(updated, before=now - PROVISIONING_STALE_AFTER),
        ),
        and_(
            status.in_((PROVISIONING_RETRY_WAIT, PROVISIONING_FAILED)),
            _due_or_malformed(next_attempt, now=now),
        ),
    )
    query = (
        db.query(Role)
        .join(Organization, Organization.id == Role.organization_id)
        .filter(
            Role.deleted_at.is_(None),
            Organization.agent_workspace_paused_at.is_(None),
            Role.assessment_task_provisioning.isnot(None),
            status.in_(sorted(PROVISIONING_RECOVERABLE_STATUSES)),
            due,
        )
        .order_by(
            marker.asc().nullsfirst(),
            Role.updated_at.asc().nullsfirst(),
            Role.id.asc(),
        )
    )
    candidates, scanned = _collect_ready(
        query,
        model=Role,
        limit=limit,
        ready=lambda role: (
            (int(role.id), int(role.organization_id))
            if provisioning_state_is_due(task_provisioning_state(role), now=now)
            else None
        ),
    )
    keys = []
    for role, key in candidates:
        payload = dict(role.assessment_task_provisioning or {})
        payload["last_sweep_dispatched_at"] = now.isoformat()
        if _claim_role_payload(db, role=role, payload=payload, now=now):
            keys.append(key)
    if scanned or candidates:
        db.commit()
    return RecoverySelection(tuple(keys), scanned)


def select_battle_recovery_batch(
    db: Session,
    *,
    limit: int,
    now: datetime,
) -> RecoverySelection:
    state = Task.extra_data["battle_test_provisioning"]
    status = state["status"].as_string()
    next_attempt = state["next_attempt_at"].as_string()
    updated = state["updated_at"].as_string()
    marker = state["last_sweep_dispatched_at"].as_string()
    verdict = Task.extra_data["battle_test"]["verdict"].as_string()
    stale = _stale_or_malformed(updated, before=now - BATTLE_TEST_STALE_AFTER)
    due = or_(
        and_(or_(status.is_(None), status == BATTLE_TEST_PENDING), verdict.is_(None)),
        and_(status == BATTLE_TEST_RUNNING, stale),
        and_(
            status.in_((BATTLE_TEST_RETRY_WAIT, BATTLE_TEST_FAILED)),
            _due_or_malformed(next_attempt, now=now),
        ),
        status == BATTLE_TEST_REPAIR_PENDING,
        and_(status == BATTLE_TEST_REPAIRING, stale),
        and_(
            status.in_((BATTLE_TEST_REPAIR_RETRY_WAIT, BATTLE_TEST_REPAIR_FAILED)),
            _due_or_malformed(next_attempt, now=now),
        ),
    )
    needs_review = Task.extra_data["needs_review"].as_boolean()
    query = (
        db.query(Task)
        .join(Organization, Organization.id == Task.organization_id)
        .filter(
            Task.organization_id.isnot(None),
            Organization.agent_workspace_paused_at.is_(None),
            Task.is_active.is_(False),
            Task.extra_data["generated"].as_boolean().is_(True),
            or_(needs_review.is_(None), needs_review.is_(True)),
            due,
        )
        .order_by(
            marker.asc().nullsfirst(),
            Task.updated_at.asc().nullsfirst(),
            Task.id.asc(),
        )
    )
    candidates, scanned = _collect_ready(
        query,
        model=Task,
        limit=limit,
        ready=lambda task: (
            (int(task.id), int(task.organization_id), action)
            if (action := battle_test_provisioning_action(task, now=now))
            else None
        ),
    )
    keys = []
    for task, key in candidates:
        extra = dict(task.extra_data or {})
        state_payload = dict(extra.get("battle_test_provisioning") or {})
        state_payload["last_sweep_dispatched_at"] = now.isoformat()
        extra["battle_test_provisioning"] = state_payload
        if _claim_task_extra(db, task=task, extra=extra, now=now):
            keys.append(key)
    if scanned or candidates:
        db.commit()
    return RecoverySelection(tuple(keys), scanned)


def _state_is_due(role: Role, *, section: str, now: datetime) -> bool:
    provisioning = role.assessment_task_provisioning or {}
    state = provisioning.get(section) or {}
    raw = state.get("next_attempt_at")
    if not raw:
        return True
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def select_role_artifact_recovery_batch(
    db: Session,
    *,
    section: str,
    limit: int,
    now: datetime,
) -> RecoverySelection:
    if section not in {
        "interview_focus_provisioning",
        "tech_questions_provisioning",
    }:
        raise ValueError(f"Unsupported recovery section: {section}")
    state = Role.assessment_task_provisioning[section]
    next_attempt = state["next_attempt_at"].as_string()
    marker = state["last_sweep_dispatched_at"].as_string()
    missing_output = (
        Role.interview_focus.is_(None)
        if section == "interview_focus_provisioning"
        else Role.tech_questions_signature.is_(None)
    )
    query = (
        db.query(Role)
        .join(Organization, Organization.id == Role.organization_id)
        .filter(
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.is_(None),
            Organization.agent_workspace_paused_at.is_(None),
            Role.job_spec_text.isnot(None),
            Role.job_spec_text != "",
            missing_output,
            _due_or_malformed(next_attempt, now=now),
        )
        .order_by(
            marker.asc().nullsfirst(),
            Role.updated_at.asc().nullsfirst(),
            Role.id.asc(),
        )
    )
    candidates, scanned = _collect_ready(
        query,
        model=Role,
        limit=limit,
        ready=lambda role: (
            int(role.id) if _state_is_due(role, section=section, now=now) else None
        ),
    )
    keys = []
    for role, key in candidates:
        payload = dict(role.assessment_task_provisioning or {})
        state_payload = dict(payload.get(section) or {})
        state_payload["last_sweep_dispatched_at"] = now.isoformat()
        payload[section] = state_payload
        if _claim_role_payload(db, role=role, payload=payload, now=now):
            keys.append(key)
    if scanned or candidates:
        db.commit()
    return RecoverySelection(tuple(keys), scanned)


__all__ = [
    "RecoverySelection",
    "select_battle_recovery_batch",
    "select_generation_recovery_batch",
    "select_role_artifact_recovery_batch",
]
