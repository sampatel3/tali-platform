"""API tests for task CRUD endpoints (/api/v1/tasks/)."""

from pathlib import Path
import uuid

from app.models.task import Task
from tests.conftest import auth_headers, create_task_via_api


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
        name="Data Platform Incident Triage and Recovery",
        description="Current demo template",
        task_type="python",
        difficulty="medium",
        duration_minutes=30,
        starter_code="print('current')",
        test_code="",
        is_template=True,
        is_active=True,
        task_key="data_eng_super_platform_crisis",
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
                "task_id": "data_eng_super_platform_crisis",
                "name": "Data Platform Incident Triage and Recovery",
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
    assert "data_eng_super_platform_crisis" in returned_keys
    assert "data_eng_b_cdc_fix" not in returned_keys

    db.refresh(stale_template)
    assert stale_template.is_active is False


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{id} — Get single
# ---------------------------------------------------------------------------


def test_get_task_success(client):
    headers, _ = auth_headers(client)
    task_id = create_task_via_api(client, headers, name="Fetch Me").json()["id"]
    resp = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Fetch Me"


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
            return "mock://taali-assessments/data_eng_super_platform_crisis"

    monkeypatch.setattr("app.domains.tasks_repository.routes.AssessmentRepositoryService", StubRepoService)

    resp = create_task_via_api(
        client,
        headers,
        task_id="data_eng_super_platform_crisis",
        repo_structure={"name": "transaction-pipeline", "files": {"README.md": "# Transaction Pipeline"}},
    )
    assert resp.status_code == 201
    assert captured["task_key"] == "data_eng_super_platform_crisis"
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
        json={"task_id": "data_eng_super_platform_crisis"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert calls["count"] >= 2
    assert calls["task_key"] == "data_eng_super_platform_crisis"
