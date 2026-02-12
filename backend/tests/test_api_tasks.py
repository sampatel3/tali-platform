"""API tests for task CRUD endpoints (/api/v1/tasks/)."""

import uuid

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
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Full Task"
    assert data["duration_minutes"] == 60


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
