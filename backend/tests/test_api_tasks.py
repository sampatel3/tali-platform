"""API tests for task CRUD endpoints (/api/v1/tasks/)."""

from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from unittest.mock import patch
import uuid

import pytest

from app.deps import get_current_user
from app.main import app
from app.models.agent_needs_input import AgentNeedsInput
from app.models.assessment import Assessment
from app.models.job_hiring_team import JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_change_event import RoleChangeEvent
from app.models.task import Task
from app.models.user import User
from app.domains.tasks_repository.task_update_command import execute_task_update
from tests.conftest import TestingSessionLocal, auth_headers, create_task_via_api


# ---------------------------------------------------------------------------
# POST /api/v1/tasks/ — Create
# ---------------------------------------------------------------------------


def test_create_task_success(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, name="My Task")
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Task"
    assert "id" in data


def test_repository_task_approval_resolves_linked_role_prompt(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role = Role(
        organization_id=int(user.organization_id),
        name="Generated-task role",
        source="manual",
        monthly_usd_budget_cents=5000,
    )
    task = Task(
        organization_id=int(user.organization_id),
        name="Generated platform exercise",
        duration_minutes=30,
        is_template=False,
        is_active=False,
        task_key=f"generated_prompt_{id(db)}",
        repo_structure={"files": {"README.md": "# Exercise"}},
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test": {"verdict": "pass"},
        },
    )
    role.tasks.append(task)
    db.add(role)
    db.flush()
    prompt = AgentNeedsInput(
        organization_id=int(user.organization_id),
        role_id=int(role.id),
        kind="task_assignment_missing",
        prompt="Choose an assessment task",
    )
    db.add(prompt)
    db.commit()

    with patch(
        "app.services.task_approval_service.provision_and_validate_task_repository",
        return_value="mock://taali-assessments/generated-prompt",
    ):
        response = client.post(
            f"/api/v1/tasks/{int(task.id)}/approve",
            headers=headers,
        )

    assert response.status_code == 200, response.text
    open_prompts = client.get(
        f"/api/v1/agent-needs-input?role_id={int(role.id)}",
        headers=headers,
    )
    assert open_prompts.status_code == 200, open_prompts.text
    assert open_prompts.json() == []
    db.expire_all()
    stored = db.query(AgentNeedsInput).filter(AgentNeedsInput.id == prompt.id).one()
    assert stored.resolved_at is not None
    assert stored.response == {"value": "auto_resolved", "auto_resolved": True}
    assert db.query(Task).filter(Task.id == task.id).one().is_active is True
    assert db.get(Role, role.id).version == 2
    event = db.query(RoleChangeEvent).filter(RoleChangeEvent.role_id == role.id).one()
    assert event.action == "role_task_approved"
    assert (event.from_version, event.to_version) == (1, 2)


def test_repository_task_approval_rejects_content_changed_during_provisioning(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    task = Task(
        organization_id=int(user.organization_id),
        name="Generated concurrent exercise",
        description="Original exact content",
        duration_minutes=30,
        is_template=False,
        is_active=False,
        task_key=f"generated_concurrent_{id(db)}",
        repo_structure={"files": {"README.md": "# Original exercise"}},
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test": {"verdict": "pass"},
        },
    )
    db.add(task)
    db.commit()
    task_id = int(task.id)

    def _provision_after_concurrent_edit(_snapshot, *, settings_obj):
        del settings_obj
        concurrent = TestingSessionLocal()
        try:
            current = concurrent.get(Task, task_id)
            current.description = "Newer recruiter-authored content"
            concurrent.commit()
        finally:
            concurrent.close()
        return "mock://taali-assessments/generated-concurrent"

    with patch(
        "app.services.task_approval_service.provision_and_validate_task_repository",
        side_effect=_provision_after_concurrent_edit,
    ):
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"].startswith("task_approval_superseded:")
    db.expire_all()
    stored = db.get(Task, task_id)
    assert stored.description == "Newer recruiter-authored content"
    assert stored.is_active is False
    assert stored.extra_data["needs_review"] is True


def test_shared_task_approval_requires_control_of_every_linked_role(client, db):
    _headers, email = auth_headers(client)
    owner = db.query(User).filter(User.email == email).one()
    member = User(
        email=f"shared-task-member-{id(db)}@test.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        organization_id=int(owner.organization_id),
        role="member",
    )
    first = Role(organization_id=owner.organization_id, name="First shared role")
    second = Role(organization_id=owner.organization_id, name="Second shared role")
    task = Task(
        organization_id=owner.organization_id,
        name="Shared generated task",
        is_active=False,
        repo_structure={"files": {"README.md": "# Task"}},
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test": {"verdict": "pass"},
        },
    )
    first.tasks.append(task)
    second.tasks.append(task)
    db.add_all([member, first, second])
    db.flush()
    first_membership = JobHiringTeam(
        organization_id=owner.organization_id,
        role_id=first.id,
        user_id=member.id,
        team_role="recruiter",
    )
    second_membership = JobHiringTeam(
        organization_id=owner.organization_id,
        role_id=second.id,
        user_id=member.id,
        team_role="interviewer",
    )
    db.add_all([first_membership, second_membership])
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: member
    try:
        denied = client.post(f"/api/v1/tasks/{task.id}/approve")
        assert denied.status_code == 403, denied.text
        db.expire_all()
        assert db.get(Task, task.id).is_active is False
        assert db.get(Role, first.id).version == 1
        assert db.get(Role, second.id).version == 1
        assert db.query(RoleChangeEvent).count() == 0

        second_membership.team_role = "recruiter"
        db.commit()
        with patch(
            "app.services.task_approval_service.provision_and_validate_task_repository",
            return_value="mock://taali-assessments/shared-generated-task",
        ):
            approved = client.post(f"/api/v1/tasks/{task.id}/approve")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert approved.status_code == 200, approved.text
    db.expire_all()
    assert db.get(Role, first.id).version == 2
    assert db.get(Role, second.id).version == 2
    events = db.query(RoleChangeEvent).order_by(RoleChangeEvent.role_id).all()
    assert [event.role_id for event in events] == [first.id, second.id]
    assert all(event.action == "role_task_approved" for event in events)


def test_stale_soft_deleted_role_link_does_not_strand_task_updates(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    task = Task(
        organization_id=user.organization_id,
        name="Task with stale role assignment",
        duration_minutes=30,
        is_active=True,
    )
    deleted_role = Role(
        organization_id=user.organization_id,
        name="Deleted role with stale task assignment",
        deleted_at=datetime.now(timezone.utc),
    )
    deleted_role.tasks.append(task)
    db.add(deleted_role)
    db.commit()

    updated = client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"duration_minutes": 45},
        headers=headers,
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["duration_minutes"] == 45
    blocked_delete = client.delete(f"/api/v1/tasks/{task.id}", headers=headers)
    assert blocked_delete.status_code == 409, blocked_delete.text
    assert blocked_delete.json()["detail"]["code"] == "TASK_STILL_REFERENCED"
    db.expire_all()
    assert db.get(Role, deleted_role.id).version == 1


def test_create_task_all_optional_fields(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(
        client,
        headers,
        name="Full Task",
        description="A thorough description here",
        task_type="javascript",
        difficulty="hard",
        duration_minutes=60,
        starter_code="console.log('hello');",
        test_code="def test_js(): pass\n",
        is_template=True,
        proctoring_enabled=True,
        claude_budget_limit_usd=5.0,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Full Task"
    assert data["duration_minutes"] == 60
    assert data["claude_budget_limit_usd"] == 5.0


def test_create_task_empty_repository_structure_builds_safe_default(client):
    headers, _ = auth_headers(client)

    response = create_task_via_api(
        client,
        headers,
        starter_code="def starter(): pass\n",
        test_code="def test_starter(): pass\n",
        repo_structure={"name": "preserved-name", "files": {}},
    )

    assert response.status_code == 201, response.text
    repo = response.json()["repo_structure"]
    assert repo["name"] == "preserved-name"
    assert set(repo["files"]) == {
        "README.md",
        "src/task.py",
        "tests/test_task.py",
    }


def test_create_task_invalid_claude_budget_rejected(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, claude_budget_limit_usd=0)
    assert resp.status_code == 422


def test_create_task_name_too_short_422(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, name="ab")
    assert resp.status_code == 422


def test_create_task_name_too_long_422(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, name="x" * 201)
    assert resp.status_code == 422


def test_create_task_missing_required_fields_422(client):
    headers, _ = auth_headers(client)
    resp = client.post("/api/v1/tasks/", json={}, headers=headers)
    assert resp.status_code == 422


def test_create_task_no_auth_401(client):
    resp = client.post(
        "/api/v1/tasks/",
        json={
            "name": "No-Auth Task",
            "description": "Should be rejected",
            "task_type": "python",
            "difficulty": "easy",
            "starter_code": "# code\n",
            "test_code": "def test(): pass\n",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/ — List
# ---------------------------------------------------------------------------


def test_list_tasks_empty(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/tasks/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # The response may be a list or a paginated dict; handle both.
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) == 0


def test_list_tasks_with_tasks(client):
    headers, _ = auth_headers(client)
    create_task_via_api(client, headers, name="Task A")
    create_task_via_api(client, headers, name="Task B")
    resp = client.get("/api/v1/tasks/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) >= 2


def test_list_tasks_no_auth_401(client):
    resp = client.get("/api/v1/tasks/")
    assert resp.status_code == 401


def test_org_owned_template_never_leaks_as_a_global_template(client, db):
    owner_headers, owner_email = auth_headers(client)
    other_headers, _ = auth_headers(client)
    owner = db.query(User).filter(User.email == owner_email).one()
    private_template = Task(
        organization_id=owner.organization_id,
        name="Private organization template",
        task_type="private-template-type",
        role="private-template-role",
        is_template=True,
        is_active=True,
        evaluation_rubric={"private": {"weight": 1.0}},
    )
    db.add(private_template)
    db.commit()

    assert client.get(
        f"/api/v1/tasks/{private_template.id}", headers=owner_headers
    ).status_code == 200
    assert client.get(
        f"/api/v1/tasks/{private_template.id}", headers=other_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/tasks/{private_template.id}/rubric", headers=other_headers
    ).status_code == 404
    listed = client.get("/api/v1/tasks/", headers=other_headers).json()
    assert private_template.id not in {row["id"] for row in listed}
    facets = client.get("/api/v1/tasks/facets", headers=other_headers).json()
    assert "private-template-role" not in facets["roles"]

    other_role = client.post(
        "/api/v1/roles",
        json={"name": "Other organization role"},
        headers=other_headers,
    ).json()
    link = client.post(
        f"/api/v1/roles/{other_role['id']}/tasks",
        json={
            "task_id": int(private_template.id),
            "expected_version": int(other_role["version"]),
        },
        headers=other_headers,
    )
    assert link.status_code == 404, link.text


def test_list_tasks_deactivates_removed_template_specs(client, db, monkeypatch):
    headers, _ = auth_headers(client)

    stale_template = Task(
        organization_id=None,
        name="Orders Pipeline Reliability Sprint",
        description="Legacy demo template",
        task_type="python",
        difficulty="medium",
        duration_minutes=15,
        starter_code="print('legacy')",
        test_code="",
        is_template=True,
        is_active=True,
        task_key="data_eng_b_cdc_fix",
    )
    current_template = Task(
        organization_id=None,
        name="AWS Glue Pipeline Recovery",
        description="Current demo template",
        task_type="python",
        difficulty="medium",
        duration_minutes=30,
        starter_code="print('current')",
        test_code="",
        is_template=True,
        is_active=True,
        task_key="data_eng_aws_glue_pipeline_recovery",
    )
    db.add(stale_template)
    db.add(current_template)
    db.commit()

    monkeypatch.setattr("app.domains.tasks_repository.routes._TEMPLATE_SYNC_ATTEMPTED", False)
    monkeypatch.setattr("app.domains.tasks_repository.routes.settings.DATABASE_URL", "postgresql://unit-test")
    monkeypatch.setattr("app.domains.tasks_repository.routes._resolve_tasks_dir", lambda: Path("."))
    monkeypatch.setattr(
        "app.domains.tasks_repository.routes.load_task_specs",
        lambda _: [
            {
                "task_id": "data_eng_aws_glue_pipeline_recovery",
                "name": "AWS Glue Pipeline Recovery",
                "role": "data_engineer",
                "duration_minutes": 30,
                "scenario": "Current task",
                "repo_structure": {"name": "repo", "files": {"README.md": "ok"}},
                "evaluation_rubric": {"quality": {"weight": 1.0}},
            }
        ],
    )

    resp = client.get("/api/v1/tasks/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    returned_keys = {task.get("task_key") for task in data}
    assert "data_eng_aws_glue_pipeline_recovery" in returned_keys
    assert "data_eng_b_cdc_fix" not in returned_keys

    db.refresh(stale_template)
    assert stale_template.is_active is False


def test_template_sync_retries_after_transient_load_failure(db, monkeypatch):
    from app.domains.tasks_repository import routes as task_routes

    attempts = {"loads": 0, "syncs": 0}

    def load_specs(_path):
        attempts["loads"] += 1
        if attempts["loads"] == 1:
            raise RuntimeError("transient catalogue read failure")
        return [{"task_id": "retry-safe-template"}]

    def sync_specs(_db, _specs):
        attempts["syncs"] += 1
        return {
            "created": 0,
            "updated": 0,
            "deactivated": 0,
            "preserved_referenced": 0,
            "version_required": 0,
        }

    monkeypatch.setattr(task_routes, "_TEMPLATE_SYNC_ATTEMPTED", False)
    monkeypatch.setattr(task_routes.settings, "DATABASE_URL", "postgresql://unit-test")
    monkeypatch.setattr(task_routes, "_resolve_tasks_dir", lambda: Path("."))
    monkeypatch.setattr(task_routes, "load_task_specs", load_specs)
    monkeypatch.setattr(task_routes, "sync_template_task_specs", sync_specs)

    task_routes._sync_template_task_specs_if_needed(db)
    assert task_routes._TEMPLATE_SYNC_ATTEMPTED is False
    task_routes._sync_template_task_specs_if_needed(db)

    assert attempts == {"loads": 2, "syncs": 1}
    assert task_routes._TEMPLATE_SYNC_ATTEMPTED is True


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{id} — Get single
# ---------------------------------------------------------------------------


def test_get_task_success(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers, name="Fetch Me").json()["id"]
    resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Fetch Me"


def test_get_task_returns_full_evaluation_rubric_for_recruiter(client):
    headers, _ = auth_headers(client)
    rubric = {"communication": {"weight": 1.0, "criteria": {"excellent": "clear", "poor": "unclear"}}}
    task_id = create_task_via_api(client, headers, evaluation_rubric=rubric).json()["id"]

    resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["evaluation_rubric"] == rubric


def test_get_task_not_found_404(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/tasks/99999", headers=headers)
    assert resp.status_code == 404


def test_get_task_no_auth_401(client):
    resp = client.get(f"/api/v1/tasks/{uuid.uuid4()}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/v1/tasks/{id} — Update
# ---------------------------------------------------------------------------


def test_update_task_name(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers).json()["id"]
    resp = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"name": "Updated Name"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


def test_update_task_duration(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers).json()["id"]
    resp = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"duration_minutes": 90},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["duration_minutes"] == 90


def test_update_task_multiple_fields(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers).json()["id"]
    resp = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"name": "Multi Update", "difficulty": "hard", "duration_minutes": 120},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Multi Update"
    assert data["difficulty"] == "hard"
    assert data["duration_minutes"] == 120


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "description",
        "task_type",
        "difficulty",
        "duration_minutes",
        "starter_code",
        "test_code",
        "is_active",
        "proctoring_enabled",
    ],
)
def test_update_task_rejects_explicit_null_for_required_state(
    client,
    db,
    field,
):
    headers, _ = auth_headers(client)
    created = create_task_via_api(client, headers).json()
    task_id = int(created["id"])

    response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={field: None},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    db.expire_all()
    assert getattr(db.get(Task, task_id), field) is not None


def test_update_task_explicit_null_can_clear_nullable_state(client, db):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(
        client,
        headers,
        calibration_prompt="Use this calibration prompt",
    ).json()["id"]

    response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"calibration_prompt": None},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["calibration_prompt"] is None
    db.expire_all()
    assert db.get(Task, task_id).calibration_prompt is None


def test_legacy_code_patch_preserves_custom_repository_files_and_metadata(client):
    headers, _ = auth_headers(client)
    custom_repo = {
        "name": "custom-runtime",
        "runtime": {"python": "3.12", "entrypoint": "src/custom.py"},
        "files": {
            "README.md": "Custom candidate instructions",
            "src/task.py": "# old starter\n",
            "tests/test_task.py": "def test_old(): pass\n",
            "src/custom.py": "print('preserve me')\n",
            "fixtures/input.json": '{"preserve": true}',
        },
    }
    task_id = create_task_via_api(
        client,
        headers,
        starter_code="# old starter\n",
        test_code="def test_old(): pass\n",
        repo_structure=custom_repo,
    ).json()["id"]

    with (
        patch("app.domains.tasks_repository.routes.recreate_task_main_repo") as recreate,
        patch(
            "app.domains.tasks_repository.routes.AssessmentRepositoryService"
        ) as repository_service,
    ):
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={
                "starter_code": "def solve(): return 42\n",
                "test_code": "def test_solve(): assert solve() == 42\n",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    repo = response.json()["repo_structure"]
    assert repo["name"] == "custom-runtime"
    assert repo["runtime"] == custom_repo["runtime"]
    assert repo["files"]["README.md"] == custom_repo["files"]["README.md"]
    assert repo["files"]["src/custom.py"] == custom_repo["files"]["src/custom.py"]
    assert repo["files"]["fixtures/input.json"] == custom_repo["files"]["fixtures/input.json"]
    assert repo["files"]["src/task.py"] == "def solve(): return 42\n"
    assert repo["files"]["tests/test_task.py"].startswith("def test_solve")
    recreate.assert_called_once()
    repository_service.return_value.create_template_repo.assert_called_once()


def test_legacy_code_patch_skips_repo_sync_when_default_file_is_unchanged(client):
    headers, _ = auth_headers(client)
    repo = {
        "name": "already-current",
        "files": {
            "README.md": "Keep this custom readme",
            "src/task.py": "def current(): return True\n",
            "src/extra.py": "EXTRA = True\n",
        },
    }
    task_id = create_task_via_api(
        client,
        headers,
        starter_code="# stale legacy mirror\n",
        repo_structure=repo,
    ).json()["id"]

    with (
        patch("app.domains.tasks_repository.routes.recreate_task_main_repo") as recreate,
        patch(
            "app.domains.tasks_repository.routes.AssessmentRepositoryService"
        ) as repository_service,
    ):
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"starter_code": "def current(): return True\n"},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["repo_structure"] == repo
    recreate.assert_not_called()
    repository_service.assert_not_called()


def test_task_patch_rejects_content_changed_during_repository_sync(client, db):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(
        client,
        headers,
        task_id=f"repository-race-{id(db)}",
        description="Original task description",
    ).json()["id"]
    prepared = {}

    class ConcurrentRepoService:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_template_repo(self, snapshot):
            prepared["task_key"] = snapshot.task_key
            prepared["description"] = snapshot.description
            concurrent = TestingSessionLocal()
            try:
                current = concurrent.get(Task, task_id)
                current.description = "Newer concurrently committed description"
                concurrent.commit()
            finally:
                concurrent.close()

    with (
        patch("app.domains.tasks_repository.routes.recreate_task_main_repo"),
        patch(
            "app.domains.tasks_repository.routes.AssessmentRepositoryService",
            ConcurrentRepoService,
        ),
    ):
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"task_id": "requested-new-task-key"},
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TASK_UPDATE_STALE"
    assert prepared == {
        "task_key": "requested-new-task-key",
        "description": "Original task description",
    }
    db.expire_all()
    stored = db.get(Task, task_id)
    assert stored.task_key != "requested-new-task-key"
    assert stored.description == "Newer concurrently committed description"


def test_task_patch_rejects_link_created_during_repository_sync(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    task_id = create_task_via_api(
        client,
        headers,
        task_id=f"repository-link-race-{id(db)}",
    ).json()["id"]

    class ConcurrentLinkRepoService:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_template_repo(self, _snapshot):
            concurrent = TestingSessionLocal()
            try:
                role = Role(
                    organization_id=int(user.organization_id),
                    name="Concurrently linked role",
                    source="manual",
                )
                role.tasks.append(concurrent.get(Task, task_id))
                concurrent.add(role)
                concurrent.commit()
            finally:
                concurrent.close()

    with (
        patch("app.domains.tasks_repository.routes.recreate_task_main_repo"),
        patch(
            "app.domains.tasks_repository.routes.AssessmentRepositoryService",
            ConcurrentLinkRepoService,
        ),
    ):
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"task_id": "stale-linked-task-key"},
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TASK_UPDATE_STALE"
    db.expire_all()
    assert db.get(Task, task_id).task_key != "stale-linked-task-key"


def test_task_policy_patch_does_not_rebuild_or_sync_repository(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers).json()["id"]

    with (
        patch("app.domains.tasks_repository.routes.recreate_task_main_repo") as recreate,
        patch(
            "app.domains.tasks_repository.routes.AssessmentRepositoryService"
        ) as repository_service,
    ):
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"score_weights": {"correctness": 0.8, "quality": 0.2}},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["score_weights"] == {
        "correctness": 0.8,
        "quality": 0.2,
    }
    recreate.assert_not_called()
    repository_service.assert_not_called()


def test_task_repository_writers_preserve_remote_commit_order(db):
    org_id = 710_001
    organization = Organization(
        id=org_id,
        name="Repository writer org",
        slug="repository-writer-org",
    )
    user = User(
        id=710_001,
        organization_id=org_id,
        email="repository-writer@example.test",
        hashed_password="x",
        is_active=True,
        is_verified=True,
    )
    task = Task(
        id=710_001,
        organization_id=org_id,
        name="Serialized repository task",
        description="Original",
        task_key="repository-original",
        duration_minutes=30,
        is_active=True,
        repo_structure={"files": {"README.md": "original"}},
    )
    db.add_all([organization, user, task])
    db.commit()
    task_id = int(task.id)
    user_id = int(user.id)

    first_remote_entered = Event()
    release_first_remote = Event()
    second_started = Event()
    second_remote_entered = Event()
    order_guard = Lock()
    remote_order = []
    remote_main = {}
    errors = []

    class OrderedRepositoryService:
        def create_template_repo(self, snapshot):
            if snapshot.task_key == "repository-first":
                first_remote_entered.set()
                assert release_first_remote.wait(timeout=5)
            else:
                second_remote_entered.set()
            with order_guard:
                remote_order.append(snapshot.task_key)
                remote_main["task_key"] = snapshot.task_key

    def write(task_key, *, second=False):
        session = TestingSessionLocal()
        try:
            if second:
                second_started.set()
            execute_task_update(
                session,
                task_id=task_id,
                payload={"task_key": task_key},
                current_user=session.get(User, user_id),
                recreate_repository=lambda _snapshot: "/tmp/staged-task",
                repository_service_factory=lambda *_args: OrderedRepositoryService(),
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            session.close()

    first = Thread(target=write, args=("repository-first",), daemon=True)
    second = Thread(
        target=write,
        args=("repository-second",),
        kwargs={"second": True},
        daemon=True,
    )
    first.start()
    assert first_remote_entered.wait(timeout=5)
    second.start()
    assert second_started.wait(timeout=5)
    assert second_remote_entered.wait(timeout=0.15) is False
    release_first_remote.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert errors == []
    assert remote_order == ["repository-first", "repository-second"]
    assert remote_main["task_key"] == "repository-second"
    db.expire_all()
    assert db.get(Task, task_id).task_key == "repository-second"


@pytest.mark.parametrize(
    "empty_replacement",
    [{}, {"name": "empty-replacement", "files": {}}],
)
def test_empty_repository_patch_preserves_existing_candidate_files(
    client,
    empty_replacement,
):
    headers, _ = auth_headers(client)
    existing = {
        "name": "preserved-repository",
        "runtime": {"entrypoint": "src/custom.py"},
        "files": {
            "README.md": "Do not erase",
            "src/custom.py": "print('still here')\n",
        },
    }
    task_id = create_task_via_api(
        client,
        headers,
        repo_structure=existing,
    ).json()["id"]

    with (
        patch("app.domains.tasks_repository.routes.recreate_task_main_repo") as recreate,
        patch(
            "app.domains.tasks_repository.routes.AssessmentRepositoryService"
        ) as repository_service,
    ):
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"repo_structure": empty_replacement},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["repo_structure"] == existing
    recreate.assert_not_called()
    repository_service.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_data", {"rows": [1, 2]}),
        ("dependencies", ["requests==2.32.0"]),
        ("success_criteria", {"required": ["idempotent"]}),
        ("test_weights", {"correctness": 0.75, "quality": 0.25}),
    ],
)
def test_task_content_with_assessment_history_requires_a_new_version(
    client,
    db,
    field,
    value,
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    task_id = create_task_via_api(client, headers).json()["id"]
    db.add(
        Assessment(
            organization_id=user.organization_id,
            task_id=task_id,
            token=f"historic-{field}-{id(db)}",
        )
    )
    db.commit()

    response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={field: value},
        headers=headers,
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TASK_VERSION_REQUIRED"
    assert detail["changed_fields"] == [field]
    db.expire_all()
    assert getattr(db.get(Task, task_id), field) is None


def test_task_activation_state_remains_editable_without_repository_rebuild(
    client,
    db,
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    task_id = create_task_via_api(client, headers).json()["id"]
    db.add(
        Assessment(
            organization_id=user.organization_id,
            task_id=task_id,
            token=f"historic-activation-{id(db)}",
        )
    )
    db.commit()

    with (
        patch("app.domains.tasks_repository.routes.recreate_task_main_repo") as recreate,
        patch(
            "app.domains.tasks_repository.routes.AssessmentRepositoryService"
        ) as repository_service,
    ):
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"is_active": False},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    recreate.assert_not_called()
    repository_service.assert_not_called()


def test_manual_task_can_be_deactivated_and_reactivated(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers).json()["id"]

    deactivated = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False

    reactivated = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"is_active": True},
        headers=headers,
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True


def test_update_task_not_found_404(client):
    headers, _ = auth_headers(client)
    resp = client.patch(
        "/api/v1/tasks/99999",
        json={"name": "Ghost"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_update_task_no_auth_401(client):
    resp = client.patch(
        f"/api/v1/tasks/{uuid.uuid4()}",
        json={"name": "No Auth"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/tasks/{id} — Delete
# ---------------------------------------------------------------------------


def test_delete_task_success(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers).json()["id"]
    resp = client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
    assert resp.status_code in (200, 204)


def test_delete_task_not_found_404(client):
    headers, _ = auth_headers(client)
    resp = client.delete("/api/v1/tasks/99999", headers=headers)
    assert resp.status_code == 404


def test_delete_task_no_auth_401(client):
    resp = client.delete(f"/api/v1/tasks/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_delete_task_then_get_404(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers).json()["id"]
    del_resp = client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
    assert del_resp.status_code in (200, 204)
    get_resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert get_resp.status_code == 404


def test_admin_template_delete_refuses_every_role_assignment(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role = Role(organization_id=user.organization_id, name="Template consumer")
    template = Task(
        organization_id=None,
        name="Referenced global template",
        task_key=f"referenced-global-{id(db)}",
        is_template=True,
        is_active=True,
    )
    role.tasks.append(template)
    db.add(role)
    db.commit()

    response = client.post(
        "/api/v1/tasks/admin/delete-template",
        json={"task_key": template.task_key},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["references"] == ["role_assignments"]
    db.expire_all()
    assert db.get(Task, template.id) is not None
    assert [task.id for task in db.get(Role, role.id).tasks] == [template.id]


def test_admin_template_delete_retains_unreferenced_cleanup(client, db):
    template = Task(
        organization_id=None,
        name="Unreferenced global template",
        task_key=f"unreferenced-global-{id(db)}",
        is_template=True,
        is_active=False,
    )
    db.add(template)
    db.commit()
    task_id = int(template.id)

    response = client.post(
        "/api/v1/tasks/admin/delete-template",
        json={"task_key": template.task_key},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(Task, task_id) is None


# ---------------------------------------------------------------------------
# Boundary — duration_minutes limits
# ---------------------------------------------------------------------------


def test_create_task_boundary_duration_15(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, duration_minutes=15)
    assert resp.status_code == 201
    assert resp.json()["duration_minutes"] == 15


def test_create_task_boundary_duration_180(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, duration_minutes=180)
    assert resp.status_code == 201
    assert resp.json()["duration_minutes"] == 180


def test_create_task_duration_below_min_422(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, duration_minutes=0)
    assert resp.status_code == 422


def test_create_task_duration_above_max_422(client):
    headers, _ = auth_headers(client)
    resp = create_task_via_api(client, headers, duration_minutes=999)
    assert resp.status_code == 422




def test_create_task_creates_template_repo(client, monkeypatch):
    headers, _ = auth_headers(client)
    captured = {}

    class StubRepoService:
        def __init__(self, github_org=None, github_token=None):
            captured["github_org"] = github_org

        def create_template_repo(self, task):
            captured["task_key"] = task.task_key
            captured["repo_structure"] = task.repo_structure
            return "mock://taali-assessments/data_eng_aws_glue_pipeline_recovery"

    monkeypatch.setattr("app.domains.tasks_repository.routes.AssessmentRepositoryService", StubRepoService)

    resp = create_task_via_api(
        client,
        headers,
        task_id="data_eng_aws_glue_pipeline_recovery",
        repo_structure={"name": "transaction-pipeline", "files": {"README.md": "# Transaction Pipeline"}},
    )
    assert resp.status_code == 201
    assert captured["task_key"] == "data_eng_aws_glue_pipeline_recovery"
    assert captured["repo_structure"]["name"] == "transaction-pipeline"


def test_update_task_recreates_template_repo(client, monkeypatch):
    headers, _ = auth_headers(client)
    calls = {"count": 0}

    class StubRepoService:
        def __init__(self, github_org=None, github_token=None):
            pass

        def create_template_repo(self, task):
            calls["count"] += 1
            calls["task_key"] = task.task_key
            return "mock://taali-assessments/updated-task"

    monkeypatch.setattr("app.domains.tasks_repository.routes.AssessmentRepositoryService", StubRepoService)

    task_id = create_task_via_api(client, headers, task_id="seed_task").json()["id"]
    resp = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"task_id": "data_eng_aws_glue_pipeline_recovery"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert calls["count"] >= 2
    assert calls["task_key"] == "data_eng_aws_glue_pipeline_recovery"
