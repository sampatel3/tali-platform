"""API tests for assessment endpoints (/api/v1/assessments/)."""

import uuid

from tests.conftest import (
    auth_headers,
    create_assessment_via_api,
    create_task_via_api,
    create_candidate_via_api,
    setup_full_environment,
)


# ---------------------------------------------------------------------------
# POST /api/v1/assessments/ — Create
# ---------------------------------------------------------------------------


def test_create_assessment_success(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    resp = create_assessment_via_api(client, headers, task["id"])
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["task_id"] == task["id"]


def test_create_assessment_generates_unique_token(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    resp1 = create_assessment_via_api(client, headers, task["id"])
    resp2 = create_assessment_via_api(client, headers, task["id"])
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    token1 = resp1.json().get("token") or resp1.json().get("candidate_token")
    token2 = resp2.json().get("token") or resp2.json().get("candidate_token")
    assert token1 is not None
    assert token2 is not None
    assert token1 != token2


def test_create_assessment_invalid_task_id_400(client):
    headers, _ = auth_headers(client)
    resp = create_assessment_via_api(client, headers, 99999)
    assert resp.status_code in (400, 404, 422)


def test_create_assessment_missing_fields_422(client):
    headers, _ = auth_headers(client)
    resp = client.post("/api/v1/assessments/", json={}, headers=headers)
    assert resp.status_code == 422


def test_create_assessment_no_auth_401(client):
    resp = client.post(
        "/api/v1/assessments/",
        json={
            "candidate_email": "nobody@example.com",
            "candidate_name": "Nobody",
            "task_id": 99999,
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/assessments/ — List
# ---------------------------------------------------------------------------


def test_list_assessments_empty(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/assessments/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) == 0


def test_list_assessments_with_data(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    create_assessment_via_api(client, headers, task["id"])
    create_assessment_via_api(client, headers, task["id"])
    resp = client.get("/api/v1/assessments/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    assert len(items) >= 2


def test_list_assessments_filter_by_status(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    create_assessment_via_api(client, headers, task["id"])
    # Filter by a status that newly created assessments should have
    resp = client.get("/api/v1/assessments/?status=pending", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", data.get("results", []))
    # All returned items should match the requested status
    for item in items:
        if "status" in item:
            assert item["status"] == "pending"


def test_list_assessments_no_auth_401(client):
    resp = client.get("/api/v1/assessments/")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/assessments/{id} — Get single
# ---------------------------------------------------------------------------


def test_get_assessment_success(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    resp = client.get(f"/api/v1/assessments/{assessment_id}", headers=env["headers"])
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == assessment_id


def test_get_assessment_not_found_404(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/assessments/99999", headers=headers)
    assert resp.status_code == 404


def test_get_assessment_no_auth_401(client):
    resp = client.get("/api/v1/assessments/99999")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/assessments/{id} — Delete
# ---------------------------------------------------------------------------


def test_delete_assessment_success(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    resp = client.delete(f"/api/v1/assessments/{assessment_id}", headers=env["headers"])
    assert resp.status_code in (200, 204)


def test_delete_assessment_not_found_404(client):
    headers, _ = auth_headers(client)
    resp = client.delete("/api/v1/assessments/99999", headers=headers)
    assert resp.status_code == 404


def test_delete_assessment_no_auth_401(client):
    resp = client.delete("/api/v1/assessments/99999")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/assessments/token/{token}/start — Start (no auth)
# ---------------------------------------------------------------------------


def test_start_assessment_invalid_token(client):
    fake_token = "nonexistent-token-value"
    resp = client.post(f"/api/v1/assessments/token/{fake_token}/start")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/assessments/{id}/resend — Resend invite
# ---------------------------------------------------------------------------


def test_resend_assessment_no_auth_401(client):
    resp = client.post("/api/v1/assessments/99999/resend")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/assessments/{id}/notes — Add note
# ---------------------------------------------------------------------------


def test_add_note_success(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    resp = client.post(
        f"/api/v1/assessments/{assessment_id}/notes",
        json={"note": "This candidate performed well in the coding section."},
        headers=env["headers"],
    )
    assert resp.status_code in (200, 201)


def test_add_note_no_auth_401(client):
    resp = client.post(
        "/api/v1/assessments/99999/notes",
        json={"note": "Unauthorized note"},
    )
    assert resp.status_code == 401


def test_add_note_empty_rejected(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    resp = client.post(
        f"/api/v1/assessments/{assessment_id}/notes",
        json={"note": ""},
        headers=env["headers"],
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# PATCH /api/v1/assessments/{id}/manual-evaluation
# ---------------------------------------------------------------------------


def test_manual_evaluation_saved_as_structured_result(client):
    headers, _ = auth_headers(client)
    rubric = {
        "correctness": {"weight": 0.6},
        "code_quality": {"weight": 0.4},
    }
    task = create_task_via_api(client, headers, evaluation_rubric=rubric).json()
    assessment = create_assessment_via_api(client, headers, task["id"]).json()

    resp = client.patch(
        f"/api/v1/assessments/{assessment['id']}/manual-evaluation",
        headers=headers,
        json={
            "category_scores": {
                "correctness": {"score": "excellent", "evidence": ["All core tests pass", "Edge cases covered"]},
                "code_quality": {"score": "good", "evidence": "Readable naming and clear structure"},
            },
            "strengths": ["Strong debugging discipline"],
            "improvements": ["Could add more comments around tricky logic"],
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    manual = payload["manual_evaluation"]
    assert payload["evaluation_result"] == manual
    assert manual["assessment_id"] == assessment["id"]
    assert manual["completed_due_to_timeout"] is False
    assert manual["overall_score"] == 8.67
    assert manual["category_scores"]["correctness"]["weight"] == 0.6
    assert manual["category_scores"]["correctness"]["evidence"] == ["All core tests pass", "Edge cases covered"]
    assert manual["category_scores"]["code_quality"]["evidence"] == ["Readable naming and clear structure"]
    assert manual["strengths"] == ["Strong debugging discipline"]
    assert manual["improvements"] == ["Could add more comments around tricky logic"]

    get_resp = client.get(f"/api/v1/assessments/{assessment['id']}", headers=headers)
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert detail["evaluation_result"] == detail["manual_evaluation"]
    assert detail["manual_evaluation"]["category_scores"]["correctness"]["evidence"][0] == "All core tests pass"


def test_manual_evaluation_rejects_scored_category_without_evidence(client):
    headers, _ = auth_headers(client)
    rubric = {"correctness": {"weight": 1.0}}
    task = create_task_via_api(client, headers, evaluation_rubric=rubric).json()
    assessment = create_assessment_via_api(client, headers, task["id"]).json()

    resp = client.patch(
        f"/api/v1/assessments/{assessment['id']}/manual-evaluation",
        headers=headers,
        json={
            "category_scores": {
                "correctness": {"score": "excellent", "evidence": []},
            },
        },
    )
    assert resp.status_code == 400
    assert "Evidence is required" in resp.json()["detail"]
