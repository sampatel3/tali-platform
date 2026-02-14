from __future__ import annotations

from tests.conftest import (
    auth_headers,
    create_assessment_via_api,
    create_candidate_via_api,
    create_task_via_api,
    login_user,
    register_user,
    verify_user,
)


def _sorted_keys(payload: dict) -> list[str]:
    return sorted(payload.keys())


def test_auth_login_contract_snapshot(client):
    email = "snapshot-auth@example.com"
    register = register_user(client, email=email, password="SnapshotPass123!")
    assert register.status_code == 201, register.text
    verify_user(email)

    login = login_user(client, email=email, password="SnapshotPass123!")
    assert login.status_code == 200, login.text
    body = login.json()
    assert _sorted_keys(body) == ["access_token", "token_type"]
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 10


def test_tasks_contract_snapshot(client):
    headers, _ = auth_headers(client, email="snapshot-tasks@example.com")
    created = create_task_via_api(
        client,
        headers,
        task_id="snapshot_task",
        name="Snapshot task",
    )
    assert created.status_code == 201, created.text
    payload = created.json()

    expected_keys = {
        "id",
        "name",
        "description",
        "task_type",
        "difficulty",
        "duration_minutes",
        "starter_code",
        "test_code",
    }
    assert expected_keys.issubset(set(payload.keys()))


def test_candidates_contract_snapshot(client):
    headers, _ = auth_headers(client, email="snapshot-candidates@example.com")
    created = create_candidate_via_api(
        client,
        headers,
        email="candidate-snapshot@example.com",
        full_name="Candidate Snapshot",
    )
    assert created.status_code == 201, created.text
    payload = created.json()

    expected_keys = {"id", "email", "full_name", "position", "created_at"}
    assert expected_keys.issubset(set(payload.keys()))


def test_assessments_contract_snapshot(client):
    headers, _ = auth_headers(client, email="snapshot-assessments@example.com")
    task = create_task_via_api(client, headers, task_id="snapshot_assessment_task")
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]

    created = create_assessment_via_api(
        client,
        headers,
        task_id=task_id,
        candidate_email="snapshot-assessment-candidate@example.com",
        candidate_name="Snapshot Candidate",
    )
    assert created.status_code == 201, created.text
    payload = created.json()

    expected_keys = {
        "id",
        "token",
        "status",
        "duration_minutes",
        "candidate_email",
        "task_id",
        "organization_id",
    }
    assert expected_keys.issubset(set(payload.keys()))

    fetch = client.get(f"/api/v1/assessments/{payload['id']}", headers=headers)
    assert fetch.status_code == 200, fetch.text
    fetched = fetch.json()
    assert expected_keys.issubset(set(fetched.keys()))


def test_billing_costs_contract_snapshot(client):
    headers, _ = auth_headers(client, email="snapshot-billing@example.com")
    response = client.get("/api/v1/billing/costs", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    expected_keys = {"deployment_env", "model", "costs", "summary", "thresholds", "alerts"}
    assert _sorted_keys({k: payload[k] for k in expected_keys}) == sorted(expected_keys)

    summary_expected = {
        "tenant_total_usd",
        "daily_spend_usd",
        "cost_per_completed_assessment_usd",
        "completed_assessments",
    }
    assert summary_expected.issubset(set(payload["summary"].keys()))
