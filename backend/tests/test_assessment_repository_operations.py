from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace

import pytest

from app.models.assessment import Assessment
from app.models.organization import Organization
from app.models.role import Role, role_tasks
from app.models.task import Task
from app.services.assessment_repository_operations import (
    create_serialized_assessment_branch,
)
from app.services.assessment_repository_service import AssessmentRepositoryError
from app.services.task_repository_serialization import task_repository_write_mutex
from tests.conftest import TestingSessionLocal


def _assessment(db, *, task_key: str = "repository-original") -> tuple[Assessment, Task, Role]:
    org = Organization(name="Serialized repository org", slug="serialized-repository-org")
    db.add(org)
    db.flush()
    task = Task(
        organization_id=int(org.id),
        name="Serialized assessment task",
        task_key=task_key,
        duration_minutes=30,
        is_active=True,
        repo_structure={"files": {"README.md": task_key}},
    )
    role = Role(
        organization_id=int(org.id),
        name="Serialized repository role",
        source="manual",
    )
    db.add_all([task, role])
    db.flush()
    role.tasks.append(task)
    assessment = Assessment(
        organization_id=int(org.id),
        task_id=int(task.id),
        role_id=int(role.id),
        token=f"serialized-{task_key}",
    )
    db.add(assessment)
    db.commit()
    return assessment, task, role


def _branch(assessment_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        repo_url="https://example.test/assessment.git",
        branch_name=f"assessment/{assessment_id}",
        clone_command="git clone example.test/assessment.git",
    )


def test_assessment_branch_waiter_refreshes_task_after_mutex_acquisition(db):
    assessment, task, _role = _assessment(db)
    assessment_id = int(assessment.id)
    task_id = int(task.id)
    waiter_started = Event()
    repository_entered = Event()
    captured: list[tuple[str | None, dict | None, int]] = []
    results = []
    errors = []

    class CapturingRepository:
        def create_assessment_branch(self, snapshot, assessment_id):
            captured.append(
                (
                    snapshot.task_key,
                    snapshot.repo_structure,
                    int(assessment_id),
                )
            )
            repository_entered.set()
            return _branch(assessment_id)

    def create_waiting_branch() -> None:
        session = TestingSessionLocal()
        try:
            waiter_started.set()
            current_assessment = session.get(Assessment, assessment_id)
            assert current_assessment is not None
            results.append(
                create_serialized_assessment_branch(
                    session,
                    CapturingRepository(),
                    current_assessment,
                    wait_for_repository=True,
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            session.close()

    waiter = Thread(target=create_waiting_branch, daemon=True)
    with task_repository_write_mutex(db, task_id=task_id):
        waiter.start()
        assert waiter_started.wait(timeout=2)
        assert repository_entered.wait(timeout=0.15) is False

        writer = TestingSessionLocal()
        try:
            current = writer.get(Task, task_id)
            assert current is not None
            current.task_key = "repository-fresh"
            current.repo_structure = {
                "files": {"README.md": "fresh committed content"}
            }
            writer.commit()
        finally:
            writer.close()

    waiter.join(timeout=3)

    assert waiter.is_alive() is False
    assert errors == []
    assert len(results) == 1
    assert captured == [
        (
            "repository-fresh",
            {"files": {"README.md": "fresh committed content"}},
            assessment_id,
        )
    ]


def test_assessment_branch_default_fails_fast_without_poisoning_session(db):
    assessment, task, _role = _assessment(db)
    assessment_id = int(assessment.id)
    task_id = int(task.id)
    attempt_started = Event()
    calls = []
    errors = []
    session_checks = []

    class UnexpectedRepository:
        def create_assessment_branch(self, snapshot, assessment_id):
            calls.append((snapshot, assessment_id))
            return _branch(assessment_id)

    def attempt_while_busy() -> None:
        session = TestingSessionLocal()
        try:
            attempt_started.set()
            current_assessment = session.get(Assessment, assessment_id)
            assert current_assessment is not None
            create_serialized_assessment_branch(
                session,
                UnexpectedRepository(),
                current_assessment,
            )
        except Exception as exc:  # pragma: no branch - asserted below
            errors.append(exc)
            # The helper maps contention but does not roll back or invalidate
            # the caller's transaction; each owning flow retains that choice.
            session_checks.append(session.get(Task, task_id).task_key)
        finally:
            session.close()

    contender = Thread(target=attempt_while_busy, daemon=True)
    with task_repository_write_mutex(db, task_id=task_id):
        contender.start()
        assert attempt_started.wait(timeout=2)
        contender.join(timeout=1)
        completed_while_mutex_was_held = not contender.is_alive()

    contender.join(timeout=3)

    assert completed_while_mutex_was_held is True
    assert contender.is_alive() is False
    assert len(errors) == 1
    assert isinstance(errors[0], AssessmentRepositoryError)
    assert "temporarily busy" in str(errors[0])
    assert session_checks == ["repository-original"]
    assert calls == []

    # A failed non-blocking attempt must not leak the local lock entry.
    recovered = create_serialized_assessment_branch(
        db,
        UnexpectedRepository(),
        assessment,
    )
    assert recovered.branch_name == f"assessment/{assessment_id}"
    assert len(calls) == 1


def test_assessment_branch_preserves_repository_error_and_releases_mutex(db):
    assessment, task, _role = _assessment(db)
    task_id = int(task.id)

    class FailingRepository:
        def create_assessment_branch(self, _snapshot, _assessment_id):
            raise AssessmentRepositoryError("remote branch creation failed")

    with pytest.raises(
        AssessmentRepositoryError,
        match="remote branch creation failed",
    ):
        create_serialized_assessment_branch(
            db,
            FailingRepository(),
            assessment,
        )

    # The original provider error is preserved and the mutex is reusable.
    with task_repository_write_mutex(db, task_id=task_id, wait=False):
        assert db.get(Task, task_id) is not None


@pytest.mark.parametrize("mutation", ["inactive", "unlinked", "foreign"])
def test_assessment_branch_waiter_revalidates_current_task_authority(db, mutation):
    assessment, task, role = _assessment(db)
    assessment_id = int(assessment.id)
    task_id = int(task.id)
    role_id = int(role.id)
    waiter_started = Event()
    repository_entered = Event()
    errors = []

    class UnexpectedRepository:
        def create_assessment_branch(self, _snapshot, _assessment_id):
            repository_entered.set()
            return _branch(assessment_id)

    def create_waiting_branch() -> None:
        session = TestingSessionLocal()
        try:
            current_assessment = session.get(Assessment, assessment_id)
            assert current_assessment is not None
            waiter_started.set()
            create_serialized_assessment_branch(
                session,
                UnexpectedRepository(),
                current_assessment,
                wait_for_repository=True,
            )
        except Exception as exc:  # pragma: no branch - asserted below
            errors.append(exc)
        finally:
            session.close()

    waiter = Thread(target=create_waiting_branch, daemon=True)
    with task_repository_write_mutex(db, task_id=task_id):
        waiter.start()
        assert waiter_started.wait(timeout=2)
        assert repository_entered.wait(timeout=0.15) is False
        writer = TestingSessionLocal()
        try:
            if mutation == "inactive":
                current = writer.get(Task, task_id)
                assert current is not None
                current.is_active = False
            elif mutation == "unlinked":
                writer.execute(
                    role_tasks.delete().where(
                        role_tasks.c.role_id == role_id,
                        role_tasks.c.task_id == task_id,
                    )
                )
            else:
                foreign = Organization(
                    name="Foreign repository org",
                    slug="foreign-repository-org",
                )
                writer.add(foreign)
                writer.flush()
                current = writer.get(Task, task_id)
                assert current is not None
                current.organization_id = int(foreign.id)
            writer.commit()
        finally:
            writer.close()

    waiter.join(timeout=3)

    assert waiter.is_alive() is False
    assert len(errors) == 1
    assert isinstance(errors[0], AssessmentRepositoryError)
    assert "no longer active and assignable" in str(errors[0])
    assert repository_entered.is_set() is False
