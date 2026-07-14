"""Unit tests for the A/B task-selection chokepoint shared by the agent send
action and the recruiter create-assessment endpoint.

resolve_task_and_variant is what makes role 26's A/B actually split: when the
caller passes task_id=None and the role has an active experiment, the arm is
drawn deterministically; when task_id is given it is recorded as forced and the
experiment is bypassed. These tests pin that contract.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.assessment_experiment import (
    ASSIGNMENT_METHOD_FORCED,
    ASSIGNMENT_METHOD_RANDOM,
    ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT,
    EXPERIMENT_STATUS_ACTIVE,
    AssessmentExperiment,
    AssessmentExperimentArm,
)
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.services.experiment_assignment import (
    RoleTaskMisconfigured,
    resolve_task_and_variant,
)


def _org(db, slug):
    org = Organization(name="O", slug=slug)
    db.add(org)
    db.flush()
    return org


def _task(db, org, key, *, active=True):
    t = Task(
        organization_id=org.id,
        name=key,
        task_key=key,
        is_template=True,
        is_active=active,
    )
    db.add(t)
    db.flush()
    return t


def _role_with_tasks(db, org, tasks):
    role = Role(organization_id=org.id, name="R", source="manual")
    db.add(role)
    db.flush()
    for t in tasks:
        role.tasks.append(t)
    db.flush()
    return role


def _experiment(db, org, role, arms, *, status=EXPERIMENT_STATUS_ACTIVE):
    exp = AssessmentExperiment(
        organization_id=org.id,
        role_id=role.id,
        key=f"exp_{role.id}",
        name="AB",
        salt="test_salt",
        status=status,
        experiment_type="task",
    )
    db.add(exp)
    db.flush()
    for arm_key, task in arms:
        db.add(
            AssessmentExperimentArm(
                experiment_id=exp.id, arm_key=arm_key, task_id=task.id, weight=1, is_active=True
            )
        )
    db.flush()
    return exp


def test_explicit_task_id_is_forced_and_bypasses_experiment(db):
    org = _org(db, "forced-org")
    a, b = _task(db, org, "task_a"), _task(db, org, "task_b")
    role = _role_with_tasks(db, org, [a, b])
    _experiment(db, org, role, [("A", a), ("B", b)])

    choice = resolve_task_and_variant(
        db, role, candidate_id=1, organization_id=org.id, task_id=b.id
    )
    assert choice.task.id == b.id
    assert choice.method == ASSIGNMENT_METHOD_FORCED


def test_no_task_id_draws_from_active_experiment_and_is_stable(db):
    org = _org(db, "ab-org")
    a, b = _task(db, org, "task_a"), _task(db, org, "task_b")
    role = _role_with_tasks(db, org, [a, b])
    _experiment(db, org, role, [("A", a), ("B", b)])

    first = resolve_task_and_variant(db, role, candidate_id=42, organization_id=org.id, task_id=None)
    assert first.method == ASSIGNMENT_METHOD_RANDOM
    assert first.task.id in {a.id, b.id}
    assert first.arm is not None and first.experiment is not None

    # Same candidate resolves to the same arm (deterministic / stable).
    again = resolve_task_and_variant(db, role, candidate_id=42, organization_id=org.id, task_id=None)
    assert again.arm.id == first.arm.id

    # Different candidates spread across arms (salt-based bucketing).
    arms_seen = {
        resolve_task_and_variant(
            db, role, candidate_id=c, organization_id=org.id, task_id=None
        ).arm.arm_key
        for c in range(1, 40)
    }
    assert arms_seen == {"A", "B"}, f"expected both arms to be drawn, saw {arms_seen}"


def test_no_task_id_single_task_role_uses_default(db):
    org = _org(db, "single-org")
    a = _task(db, org, "only_task")
    role = _role_with_tasks(db, org, [a])

    choice = resolve_task_and_variant(db, role, candidate_id=1, organization_id=org.id, task_id=None)
    assert choice.task.id == a.id
    assert choice.method == ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT


def test_no_task_id_multiple_tasks_no_experiment_is_misconfigured(db):
    org = _org(db, "ambiguous-org")
    a, b = _task(db, org, "task_a"), _task(db, org, "task_b")
    role = _role_with_tasks(db, org, [a, b])

    with pytest.raises(RoleTaskMisconfigured):
        resolve_task_and_variant(db, role, candidate_id=1, organization_id=org.id, task_id=None)


def test_inactive_draft_is_ignored_for_default_selection(db):
    org = _org(db, "ignore-draft-org")
    active = _task(db, org, "live_task")
    draft = _task(db, org, "draft_task", active=False)
    role = _role_with_tasks(db, org, [active, draft])

    choice = resolve_task_and_variant(
        db, role, candidate_id=1, organization_id=org.id, task_id=None
    )
    assert choice.task.id == active.id
    assert choice.method == ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT


def test_explicit_inactive_task_is_refused(db):
    org = _org(db, "refuse-draft-org")
    draft = _task(db, org, "draft_task", active=False)
    role = _role_with_tasks(db, org, [draft])

    with pytest.raises(HTTPException) as exc_info:
        resolve_task_and_variant(
            db, role, candidate_id=1, organization_id=org.id, task_id=draft.id
        )
    assert exc_info.value.status_code == 422
    assert "inactive" in str(exc_info.value.detail).lower()


def test_role_with_only_inactive_draft_is_misconfigured(db):
    org = _org(db, "draft-only-org")
    draft = _task(db, org, "draft_task", active=False)
    role = _role_with_tasks(db, org, [draft])

    with pytest.raises(RoleTaskMisconfigured, match="no active tasks"):
        resolve_task_and_variant(
            db, role, candidate_id=1, organization_id=org.id, task_id=None
        )


def test_experiment_ignores_arm_whose_task_is_inactive(db):
    org = _org(db, "inactive-arm-org")
    active = _task(db, org, "live_task")
    draft = _task(db, org, "draft_task", active=False)
    role = _role_with_tasks(db, org, [active, draft])
    _experiment(db, org, role, [("live", active), ("draft", draft)])

    choices = {
        resolve_task_and_variant(
            db, role, candidate_id=i, organization_id=org.id, task_id=None
        ).task.id
        for i in range(1, 20)
    }
    assert choices == {active.id}
