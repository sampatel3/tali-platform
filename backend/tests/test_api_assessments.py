"""API tests for assessment endpoints (/api/v1/assessments/)."""

import uuid
from types import SimpleNamespace

from app.domains.assessments_runtime import candidate_runtime_routes as candidate_runtime_module
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task
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


def test_create_assessment_creates_branch_on_assignment(client, monkeypatch):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers, task_id="prod_branch_task").json()
    captured = {}

    class StubRepoService:
        def __init__(self, github_org=None, github_token=None):
            captured["github_org"] = github_org

        def create_assessment_branch(self, task_obj, assessment_id):
            captured["task_key"] = task_obj.task_key
            captured["assessment_id"] = assessment_id
            return SimpleNamespace(
                repo_url="https://github.com/taali-assessments/prod_branch_task.git",
                branch_name=f"assessment/{assessment_id}",
                clone_command=f"git clone --branch assessment/{assessment_id} https://github.com/taali-assessments/prod_branch_task.git",
            )

    monkeypatch.setattr(
        "app.domains.assessments_runtime.recruiter_management_routes.AssessmentRepositoryService",
        StubRepoService,
    )

    resp = create_assessment_via_api(client, headers, task["id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["assessment_branch"] == f"assessment/{body['id']}"
    assert body["assessment_repo_url"] == "https://github.com/taali-assessments/prod_branch_task.git"
    assert "git clone --branch" in body["clone_command"]
    assert captured["task_key"] == "prod_branch_task"
    assert captured["assessment_id"] == body["id"]


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


def test_demo_start_creates_lead_and_demo_assessment(client, db, monkeypatch):
    cdc_task = Task(
        organization_id=None,
        name="Fix the Broken Data Sync",
        description="CDC sync discrepancies demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=10,
        starter_code="print('demo')",
        test_code="def test_placeholder():\n    assert True\n",
        task_key="data_eng_b_cdc_fix",
    )
    backfill_task = Task(
        organization_id=None,
        name="Historical Backfill and Schema Evolution",
        description="Backfill + schema evolution demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=10,
        starter_code="print('demo')",
        test_code="",
        task_key="data_eng_c_backfill_schema",
    )
    db.add(cdc_task)
    db.add(backfill_task)
    db.commit()
    db.refresh(cdc_task)
    db.refresh(backfill_task)

    def fake_start_or_resume(assessment, _db):
        selected_task = _db.query(Task).filter(Task.id == assessment.task_id).first()
        return {
            "assessment_id": assessment.id,
            "token": assessment.token,
            "sandbox_id": "sandbox-demo",
            "task": {
                "name": selected_task.name if selected_task else "Unknown task",
                "description": selected_task.description if selected_task else "",
                "starter_code": selected_task.starter_code if selected_task else "",
                "duration_minutes": assessment.duration_minutes,
                "task_key": selected_task.task_key if selected_task else None,
                "role": selected_task.role if selected_task else None,
                "scenario": selected_task.scenario if selected_task else None,
                "repo_structure": selected_task.repo_structure if selected_task else None,
                "rubric_categories": [],
                "evaluation_rubric": None,
                "extra_data": None,
                "calibration_prompt": None,
                "proctoring_enabled": False,
                "claude_budget_limit_usd": None,
            },
            "claude_budget": {"enabled": False},
            "time_remaining": 1800,
            "is_timer_paused": False,
            "pause_reason": None,
            "total_paused_seconds": 0,
        }

    monkeypatch.setattr(candidate_runtime_module, "start_or_resume_assessment", fake_start_or_resume)

    payload = {
        "full_name": "Demo User",
        "position": "Engineering Manager",
        "email": "demo-user@example.com",
        "work_email": "demo-user@company.com",
        "company_name": "Acme Corp",
        "company_size": "51-200",
        "assessment_track": "data_eng_b_cdc_fix",
        "marketing_consent": True,
    }
    resp = client.post("/api/v1/assessments/demo/start", json=payload)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["assessment_id"] > 0
    assert body["token"]
    assert body["sandbox_id"] == "sandbox-demo"

    demo_org = db.query(Organization).filter(Organization.slug == "taali-demo").first()
    assert demo_org is not None

    candidate = db.query(Candidate).filter(Candidate.email == "demo-user@example.com").first()
    assert candidate is not None
    assert candidate.organization_id == demo_org.id
    assert candidate.work_email == "demo-user@company.com"
    assert candidate.company_name == "Acme Corp"
    assert candidate.company_size == "51-200"
    assert candidate.lead_source == "landing_demo"
    assert candidate.marketing_consent is True

    assessment = db.query(Assessment).filter(Assessment.id == body["assessment_id"]).first()
    assert assessment is not None
    assert assessment.is_demo is True
    assert assessment.demo_track == "data_eng_b_cdc_fix"
    assert assessment.task_id == cdc_task.id
    assert assessment.demo_profile["work_email"] == "demo-user@company.com"
    assert assessment.demo_profile["marketing_consent"] is True
    assert body["task"]["task_key"] == "data_eng_b_cdc_fix"


def test_demo_start_uses_selected_track_task(client, db, monkeypatch):
    cdc_task = Task(
        organization_id=None,
        name="Fix the Broken Data Sync",
        description="CDC sync discrepancies demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=10,
        starter_code="print('demo')",
        test_code="",
        task_key="data_eng_b_cdc_fix",
    )
    backfill_task = Task(
        organization_id=None,
        name="Historical Backfill and Schema Evolution",
        description="Backfill + schema evolution demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=10,
        starter_code="print('demo')",
        test_code="",
        task_key="data_eng_c_backfill_schema",
    )
    db.add(cdc_task)
    db.add(backfill_task)
    db.commit()
    db.refresh(cdc_task)
    db.refresh(backfill_task)

    def fake_start_or_resume(assessment, _db):
        selected_task = _db.query(Task).filter(Task.id == assessment.task_id).first()
        return {
            "assessment_id": assessment.id,
            "token": assessment.token,
            "sandbox_id": "sandbox-demo",
            "task": {
                "name": selected_task.name if selected_task else "Unknown task",
                "description": selected_task.description if selected_task else "",
                "starter_code": selected_task.starter_code if selected_task else "",
                "duration_minutes": assessment.duration_minutes,
                "task_key": selected_task.task_key if selected_task else None,
                "role": selected_task.role if selected_task else None,
                "scenario": selected_task.scenario if selected_task else None,
                "repo_structure": selected_task.repo_structure if selected_task else None,
                "rubric_categories": [],
                "evaluation_rubric": None,
                "extra_data": None,
                "calibration_prompt": None,
                "proctoring_enabled": False,
                "claude_budget_limit_usd": None,
            },
            "claude_budget": {"enabled": False},
            "time_remaining": 1200,
            "is_timer_paused": False,
            "pause_reason": None,
            "total_paused_seconds": 0,
        }

    monkeypatch.setattr(candidate_runtime_module, "start_or_resume_assessment", fake_start_or_resume)

    payload = {
        "full_name": "Frontend Demo User",
        "position": "Engineering Director",
        "email": "frontend-demo@example.com",
        "work_email": "frontend-demo@company.com",
        "company_name": "Frontend Co",
        "company_size": "11-50",
        "assessment_track": "data_eng_c_backfill_schema",
        "marketing_consent": True,
    }
    resp = client.post("/api/v1/assessments/demo/start", json=payload)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assessment = db.query(Assessment).filter(Assessment.id == body["assessment_id"]).first()
    assert assessment is not None
    assert assessment.demo_track == "data_eng_c_backfill_schema"
    assert assessment.task_id == backfill_task.id
    assert body["task"]["task_key"] == "data_eng_c_backfill_schema"


def test_demo_start_accepts_legacy_track_keys(client, db, monkeypatch):
    cdc_task = Task(
        organization_id=None,
        name="Fix the Broken Data Sync",
        description="CDC sync discrepancies demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=10,
        starter_code="print('demo')",
        test_code="",
        task_key="data_eng_b_cdc_fix",
    )
    db.add(cdc_task)
    db.commit()
    db.refresh(cdc_task)

    def fake_start_or_resume(assessment, _db):
        selected_task = _db.query(Task).filter(Task.id == assessment.task_id).first()
        return {
            "assessment_id": assessment.id,
            "token": assessment.token,
            "sandbox_id": "sandbox-demo",
            "task": {
                "name": selected_task.name if selected_task else "Unknown task",
                "description": selected_task.description if selected_task else "",
                "starter_code": selected_task.starter_code if selected_task else "",
                "duration_minutes": assessment.duration_minutes,
                "task_key": selected_task.task_key if selected_task else None,
                "role": selected_task.role if selected_task else None,
                "scenario": selected_task.scenario if selected_task else None,
                "repo_structure": selected_task.repo_structure if selected_task else None,
                "rubric_categories": [],
                "evaluation_rubric": None,
                "extra_data": None,
                "calibration_prompt": None,
                "proctoring_enabled": False,
                "claude_budget_limit_usd": None,
            },
            "claude_budget": {"enabled": False},
            "time_remaining": 1200,
            "is_timer_paused": False,
            "pause_reason": None,
            "total_paused_seconds": 0,
        }

    monkeypatch.setattr(candidate_runtime_module, "start_or_resume_assessment", fake_start_or_resume)

    payload = {
        "full_name": "Legacy Track User",
        "position": "Engineer",
        "email": "legacy-track@example.com",
        "work_email": "legacy-track@company.com",
        "company_name": "Legacy Co",
        "company_size": "11-50",
        "assessment_track": "backend-reliability",
        "marketing_consent": True,
    }
    resp = client.post("/api/v1/assessments/demo/start", json=payload)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assessment = db.query(Assessment).filter(Assessment.id == body["assessment_id"]).first()
    assert assessment is not None
    assert assessment.demo_track == "backend-reliability"
    assert assessment.task_id == cdc_task.id
    assert body["task"]["task_key"] == "data_eng_b_cdc_fix"


def test_demo_start_rejects_invalid_track(client):
    payload = {
        "full_name": "Demo User",
        "position": "Engineer",
        "email": "demo-invalid@example.com",
        "work_email": "demo-invalid@company.com",
        "company_name": "Acme Corp",
        "company_size": "11-50",
        "assessment_track": "non-existent-track",
        "marketing_consent": True,
    }
    resp = client.post("/api/v1/assessments/demo/start", json=payload)
    assert resp.status_code == 400
    assert "Unsupported demo assessment track" in resp.json()["detail"]


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
