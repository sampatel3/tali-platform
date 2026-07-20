from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.orm import Query

from app.domains.assessments_runtime import roles_management_routes
from app.domains.tasks_repository import routes as task_routes
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from app.schemas.role import RoleTaskLinkRequest
from app.schemas.task import TaskUpdate
from app.services import task_catalog
from app.services.task_approval_service import approve_task_for_use
from app.services.task_mutation_guard import lock_task_mutation_boundary
from app.services.task_provisioning_state import request_assessment_task_provisioning


def _org(db) -> Organization:
    org = Organization(name="Task Lock Org", slug=f"task-lock-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _role(db, org: Organization, *, name: str = "Engineer") -> Role:
    role = Role(
        organization_id=int(org.id),
        name=name,
        source="manual",
        version=1,
        auto_skip_assessment=False,
    )
    db.add(role)
    db.flush()
    return role


def _user(db, org: Organization) -> User:
    user = User(
        email=f"task-lock-{id(db)}@example.test",
        hashed_password="x",
        full_name="Owner",
        role="owner",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return user


def _task(
    db,
    org: Organization,
    *,
    active: bool = True,
    generated: bool = False,
) -> Task:
    task = Task(
        organization_id=int(org.id),
        name="Concurrency task",
        task_key=f"task-lock-{id(db)}-{active}-{generated}",
        duration_minutes=30,
        is_active=active,
        repo_structure={"name": "repo", "files": {"README.md": "instructions"}},
        extra_data=(
            {
                "generated": True,
                "needs_review": True,
                "battle_test": {"verdict": "pass"},
            }
            if generated
            else None
        ),
    )
    db.add(task)
    db.flush()
    return task


def test_task_mutation_boundary_locks_organization_role_task_in_order(db, monkeypatch):
    org = _org(db)
    role_b = _role(db, org, name="B")
    role_a = _role(db, org, name="A")
    task = _task(db, org)
    role_b.tasks.append(task)
    role_a.tasks.append(task)
    db.commit()

    locked_entities: list[type] = []
    original = Query.with_for_update

    def recording_lock(query, *args, **kwargs):
        entity = query.column_descriptions[0].get("entity")
        locked_entities.append(entity)
        return original(query, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", recording_lock)
    boundary = lock_task_mutation_boundary(db, task_ids=[int(task.id)])

    assert locked_entities == [Organization, Role, Task]
    assert [row.id for row in boundary.organizations] == [org.id]
    assert [row.id for row in boundary.roles] == sorted([role_a.id, role_b.id])
    assert [row.id for row in boundary.tasks] == [task.id]


def test_catalog_deactivation_locks_linked_role_before_changing_task(db):
    org = _org(db)
    role = _role(db, org)
    task = Task(
        organization_id=None,
        name="Legacy template",
        task_key="legacy-template-lock-test",
        is_template=True,
        is_active=True,
    )
    db.add(task)
    db.flush()
    role.tasks.append(task)
    db.commit()

    real_lock = lock_task_mutation_boundary
    observed: list[str] = []

    def lock_before_mutation(session, **kwargs):
        assert db.get(Task, int(task.id)).is_active is True
        observed.append("locked")
        return real_lock(session, **kwargs)

    with patch(
        "app.services.task_mutation_guard.lock_task_mutation_boundary",
        side_effect=lock_before_mutation,
    ):
        stats = task_catalog.sync_template_task_specs(db, [])

    assert observed == ["locked"]
    assert stats["deactivated"] == 1
    assert db.get(Task, int(task.id)).is_active is False


def test_generated_task_approval_locks_before_repository_provisioning(db):
    org = _org(db)
    role = _role(db, org)
    task = _task(db, org, active=False, generated=True)
    role.tasks.append(task)
    db.commit()
    role.name = "Unsaved role edit in same approval transaction"
    observed: list[str] = []
    real_lock = lock_task_mutation_boundary

    def record_lock(session, **kwargs):
        observed.append("locked")
        return real_lock(session, **kwargs)

    def provision(_task, **_kwargs):
        assert observed == ["locked"]
        observed.append("provisioned")
        return "mock://repo/main"

    with (
        patch(
            "app.services.task_mutation_guard.lock_task_mutation_boundary",
            side_effect=record_lock,
        ),
        patch(
            "app.services.task_approval_service.provision_and_validate_task_repository",
            side_effect=provision,
        ),
    ):
        approve_task_for_use(db, task, user_id=None)

    assert observed == ["locked", "provisioned"]
    assert task.is_active is True
    assert role.name == "Unsaved role edit in same approval transaction"


def test_generated_task_supersession_locks_full_boundary_and_uses_fresh_row(
    db,
    monkeypatch,
):
    org = _org(db)
    role = _role(db, org)
    task = _task(db, org, active=False, generated=True)
    role.tasks.append(task)
    db.commit()

    locked_entities: list[type] = []
    original = Query.with_for_update

    def recording_lock(query, *args, **kwargs):
        locked_entities.append(query.column_descriptions[0].get("entity"))
        return original(query, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", recording_lock)
    requested = request_assessment_task_provisioning(
        role,
        reason="job_spec_changed",
        supersede_generated_drafts=True,
    )

    assert locked_entities[:3] == [Organization, Role, Task]
    assert requested is True
    assert list(role.tasks) == []
    assert task.extra_data["superseded"] is True
    assert task.extra_data["needs_review"] is False


def test_active_task_edit_locks_before_repository_write(db):
    org = _org(db)
    role = _role(db, org)
    user = _user(db, org)
    task = _task(db, org)
    role.tasks.append(task)
    db.commit()
    observed: list[str] = []
    real_lock = lock_task_mutation_boundary

    def record_lock(session, **kwargs):
        observed.append("locked")
        return real_lock(session, **kwargs)

    class Repo:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_template_repo(self, _task):
            assert observed == ["locked"]
            observed.append("repository")

    with (
        patch.object(task_routes, "lock_task_mutation_boundary", side_effect=record_lock),
        patch.object(task_routes, "AssessmentRepositoryService", Repo),
        patch.object(task_routes, "recreate_task_main_repo", return_value="/tmp/mock"),
    ):
        result = task_routes.update_task(
            int(task.id),
            TaskUpdate(name="Updated concurrency task"),
            db=db,
            current_user=user,
        )

    assert observed == ["locked", "repository"]
    assert result.name == "Updated concurrency task"


def test_role_unlink_locks_workspace_before_role(db):
    org = _org(db)
    role = _role(db, org)
    user = _user(db, org)
    task = _task(db, org)
    role.tasks.append(task)
    db.commit()
    observed: list[str] = []
    real_workspace_lock = roles_management_routes.workspace_agent_control_snapshot
    real_permission = roles_management_routes.require_job_permission

    def workspace_lock(*args, **kwargs):
        observed.append("organization")
        return real_workspace_lock(*args, **kwargs)

    def permission(*args, **kwargs):
        assert observed == ["organization"]
        observed.append("role")
        return real_permission(*args, **kwargs)

    with (
        patch.object(
            roles_management_routes,
            "workspace_agent_control_snapshot",
            side_effect=workspace_lock,
        ),
        patch.object(
            roles_management_routes,
            "require_job_permission",
            side_effect=permission,
        ),
    ):
        roles_management_routes.remove_role_task(
            int(role.id),
            int(task.id),
            expected_version=1,
            db=db,
            current_user=user,
        )

    assert observed == ["organization", "role"]
    db.expire(role, ["tasks"])
    assert list(role.tasks) == []


def test_role_link_locks_organization_role_task_in_order(db, monkeypatch):
    org = _org(db)
    role = _role(db, org)
    user = _user(db, org)
    task = _task(db, org)
    db.commit()
    locked_entities: list[type] = []
    original = Query.with_for_update

    def recording_lock(query, *args, **kwargs):
        locked_entities.append(query.column_descriptions[0].get("entity"))
        return original(query, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", recording_lock)
    roles_management_routes.add_role_task(
        int(role.id),
        RoleTaskLinkRequest(task_id=int(task.id), expected_version=1),
        db=db,
        current_user=user,
    )

    assert locked_entities[:3] == [Organization, Role, Task]
    db.expire(role, ["tasks"])
    assert [linked.id for linked in role.tasks] == [task.id]
