"""End-to-end assessment flow tests — full lifecycle from creation to scoring."""
import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from tests.conftest import (
    auth_headers,
    create_task_via_api,
    create_candidate_via_api,
    create_assessment_via_api,
    setup_full_environment,
)


pytestmark = pytest.mark.e2e


# ===================================================================
# FULL ASSESSMENT LIFECYCLE
# ===================================================================


def test_full_assessment_lifecycle(client):
    """Create task → candidate → assessment → verify token generated."""
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    candidate = create_candidate_via_api(client, headers).json()
    assessment = create_assessment_via_api(
        client, headers, task["id"],
        candidate_email=candidate["email"],
        candidate_name=candidate.get("full_name", "Test Candidate"),
    ).json()
    assert assessment["status"] == "pending"
    assert "token" in assessment
    assert len(assessment["token"]) > 10


def test_assessment_token_uniqueness(client):
    """Multiple assessments should have unique tokens."""
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    tokens = set()
    for i in range(5):
        resp = create_assessment_via_api(client, headers, task["id"])
        assert resp.status_code == 201
        tokens.add(resp.json()["token"])
    assert len(tokens) == 5, "All assessment tokens should be unique"


def test_assessment_linked_to_correct_task(client):
    """Assessment should reference the correct task."""
    env = setup_full_environment(client)
    assessment = env["assessment"]
    assert assessment["task_id"] == env["task"]["id"]


def test_assessment_linked_to_candidate(client):
    """Assessment should reference the candidate."""
    env = setup_full_environment(client)
    assessment = env["assessment"]
    assert assessment["candidate_id"] == env["candidate"]["id"]


def test_assessment_list_reflects_status(client):
    """List endpoint should show assessments with correct statuses."""
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    create_assessment_via_api(client, headers, task["id"])
    create_assessment_via_api(client, headers, task["id"])
    resp = client.get("/api/v1/assessments/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("assessments", []))
    assert len(items) >= 2
    for item in items:
        assert item["status"] == "pending"


def test_multiple_assessments_for_same_candidate(client):
    """A candidate can have multiple assessments."""
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    cand = create_candidate_via_api(client, headers).json()
    for _ in range(3):
        resp = create_assessment_via_api(client, headers, task["id"],
                                         candidate_email=cand["email"],
                                         candidate_name=cand.get("full_name", "Test"))
        assert resp.status_code == 201


def test_assessment_shows_in_get_after_creation(client):
    """GET assessment by ID should return the created assessment."""
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    resp = client.get(f"/api/v1/assessments/{assessment_id}", headers=env["headers"])
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == assessment_id
    assert data["status"] == "pending"


def test_start_assessment_with_invalid_token_404(client):
    """Starting an assessment with a fake token should return 404."""
    resp = client.post("/api/v1/assessments/token/fake-nonexistent-token-xyz/start")
    assert resp.status_code == 404


def test_assessment_deletion(client):
    """Deleting an assessment then getting it should return 404."""
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    del_resp = client.delete(f"/api/v1/assessments/{assessment_id}", headers=env["headers"])
    assert del_resp.status_code in (200, 204)
    get_resp = client.get(f"/api/v1/assessments/{assessment_id}", headers=env["headers"])
    assert get_resp.status_code == 404


def test_assessment_with_different_tasks(client):
    """Assessments with different tasks should have different task_ids."""
    headers, _ = auth_headers(client)
    task1 = create_task_via_api(client, headers, name="Task Alpha").json()
    task2 = create_task_via_api(client, headers, name="Task Beta").json()
    a1 = create_assessment_via_api(client, headers, task1["id"]).json()
    a2 = create_assessment_via_api(client, headers, task2["id"]).json()
    assert a1["task_id"] == task1["id"]
    assert a2["task_id"] == task2["id"]
    assert a1["task_id"] != a2["task_id"]
