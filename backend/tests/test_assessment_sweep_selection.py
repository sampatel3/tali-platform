from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.services.assessment_sweep_selection import (
    select_battle_recovery_batch,
    select_generation_recovery_batch,
    select_role_artifact_recovery_batch,
)
from app.services.role_activation_intent import request_role_activation_intent
from app.services.role_activation_recovery import (
    _claim_sweep_dispatch,
    select_activation_recovery_batch,
)
from tests.conftest import TestingSessionLocal


def _org(db, suffix: str) -> Organization:
    org = Organization(name=f"Sweep {suffix}", slug=f"sweep-{suffix}-{id(db)}")
    db.add(org)
    db.flush()
    return org


def test_generation_selection_filters_future_retries_before_limit(db):
    now = datetime.now(timezone.utc)
    org = _org(db, "generation")
    for index in range(3):
        db.add(
            Role(
                organization_id=org.id,
                name=f"Future generation {index}",
                assessment_task_provisioning={
                    "status": "retry_wait",
                    "next_attempt_at": (now + timedelta(hours=1)).isoformat(),
                },
            )
        )
    due = Role(
        organization_id=org.id,
        name="Due generation",
        assessment_task_provisioning={"status": "pending", "request_id": "due"},
    )
    db.add(due)
    db.commit()

    batch = select_generation_recovery_batch(db, limit=1, now=now)

    assert batch.keys == ((int(due.id), int(org.id)),)
    assert batch.scanned == 1


def test_battle_selection_filters_future_retries_before_limit(db):
    now = datetime.now(timezone.utc)
    org = _org(db, "battle")
    for index in range(3):
        db.add(
            Task(
                organization_id=org.id,
                name=f"Future battle {index}",
                is_active=False,
                extra_data={
                    "generated": True,
                    "needs_review": True,
                    "battle_test_provisioning": {
                        "status": "retry_wait",
                        "next_attempt_at": (now + timedelta(hours=1)).isoformat(),
                    },
                },
            )
        )
    due = Task(
        organization_id=org.id,
        name="Due battle",
        is_active=False,
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test_provisioning": {"status": "pending"},
        },
    )
    db.add(due)
    db.commit()

    batch = select_battle_recovery_batch(db, limit=1, now=now)

    assert batch.keys == ((int(due.id), int(org.id), "battle_test"),)
    assert batch.scanned == 1


def test_artifact_selection_filters_future_retries_before_limit(db):
    now = datetime.now(timezone.utc)
    org = _org(db, "artifacts")
    for index in range(3):
        next_attempt = (now + timedelta(hours=1)).isoformat()
        db.add(
            Role(
                organization_id=org.id,
                name=f"Future artifacts {index}",
                agentic_mode_enabled=True,
                job_spec_text="A complete job description",
                assessment_task_provisioning={
                    "interview_focus_provisioning": {
                        "status": "retry_wait",
                        "next_attempt_at": next_attempt,
                    },
                    "tech_questions_provisioning": {
                        "status": "retry_wait",
                        "next_attempt_at": next_attempt,
                    },
                },
            )
        )
    due = Role(
        organization_id=org.id,
        name="Due artifacts",
        agentic_mode_enabled=True,
        job_spec_text="A complete job description",
        assessment_task_provisioning={
            "interview_focus_provisioning": {"status": "pending"},
            "tech_questions_provisioning": {"status": "pending"},
        },
    )
    db.add(due)
    db.commit()

    focus = select_role_artifact_recovery_batch(
        db,
        section="interview_focus_provisioning",
        limit=1,
        now=now,
    )
    tech = select_role_artifact_recovery_batch(
        db,
        section="tech_questions_provisioning",
        limit=1,
        now=now,
    )

    assert focus.keys == (int(due.id),)
    assert tech.keys == (int(due.id),)
    assert focus.scanned == tech.scanned == 1


def test_activation_claim_cas_rejects_a_competing_stale_snapshot(db):
    now = datetime.now(timezone.utc)
    org = _org(db, "activation-cas")
    role = Role(organization_id=org.id, name="CAS activation")
    task = Task(
        organization_id=org.id,
        name="Passing task",
        is_active=False,
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test": {"verdict": "pass"},
        },
    )
    role.tasks.append(task)
    db.add(role)
    db.flush()
    intent = request_role_activation_intent(
        role,
        user_id=1,
        monthly_budget_cents=5000,
    )
    db.commit()

    first_session = TestingSessionLocal()
    second_session = TestingSessionLocal()
    try:
        first = first_session.get(Role, role.id)
        stale = second_session.get(Role, role.id)
        second_session.expunge(stale)
        second_session.rollback()
        assert _claim_sweep_dispatch(
            first_session,
            role=first,
            request_id=intent["request_id"],
            now=now,
        )
        first_session.commit()
        assert not _claim_sweep_dispatch(
            second_session,
            role=stale,
            request_id=intent["request_id"],
            now=now + timedelta(microseconds=1),
        )
        second_session.rollback()
    finally:
        first_session.close()
        second_session.close()


def test_inconsistent_activation_candidate_releases_scanned_locks(db):
    now = datetime.now(timezone.utc)
    org = _org(db, "inconsistent-activation")
    role = Role(
        organization_id=org.id,
        name="Inconsistent activation",
        assessment_task_provisioning={
            "activation_intent": {
                "status": "pending",
                "request_id": "inconsistent",
                "task_id": 999999,
            }
        },
    )
    role.tasks.append(
        Task(organization_id=org.id, name="Different active task", is_active=True)
    )
    db.add(role)
    db.commit()

    batch = select_activation_recovery_batch(db, limit=1, now=now)

    assert batch.scanned == 1
    assert batch.keys == ()
    assert db.in_transaction() is False


def test_non_positive_recovery_limits_do_not_scan_or_claim_ready_work(db):
    now = datetime.now(timezone.utc)
    org = _org(db, "zero-limit")
    generation = Role(
        organization_id=org.id,
        name="Ready generation",
        assessment_task_provisioning={"status": "pending"},
    )
    battle = Task(
        organization_id=org.id,
        name="Ready battle test",
        is_active=False,
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test_provisioning": {"status": "pending"},
        },
    )
    artifact = Role(
        organization_id=org.id,
        name="Ready artifacts",
        agentic_mode_enabled=True,
        job_spec_text="A complete job description",
        assessment_task_provisioning={
            "interview_focus_provisioning": {"status": "pending"}
        },
    )
    activation = Role(organization_id=org.id, name="Ready activation")
    activation.tasks.append(
        Task(
            organization_id=org.id,
            name="Passing activation task",
            is_active=False,
            extra_data={
                "generated": True,
                "needs_review": True,
                "battle_test": {"verdict": "pass"},
            },
        )
    )
    db.add_all([generation, battle, artifact, activation])
    db.flush()
    request_role_activation_intent(
        activation,
        user_id=2,
        monthly_budget_cents=5000,
    )
    db.commit()

    batches = (
        select_generation_recovery_batch(db, limit=0, now=now),
        select_battle_recovery_batch(db, limit=-1, now=now),
        select_role_artifact_recovery_batch(
            db,
            section="interview_focus_provisioning",
            limit=0,
            now=now,
        ),
        select_activation_recovery_batch(db, limit=-1, now=now),
    )

    assert all(batch.keys == () and batch.scanned == 0 for batch in batches)
    assert batches[-1].blocked == 0
    assert db.in_transaction() is False
