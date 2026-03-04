"""API tests for assessment endpoints (/api/v1/assessments/)."""

from datetime import datetime, timezone
import io
from types import SimpleNamespace

from PyPDF2 import PdfReader
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.domains.assessments_runtime import candidate_runtime_routes as candidate_runtime_module
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task
from app.models.user import User
from app.services.candidate_feedback_engine import _completed_assessment_query_filter
from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
    create_assessment_via_api,
    create_task_via_api,
    create_candidate_via_api,
    setup_full_environment,
)


# ---------------------------------------------------------------------------
# POST /api/v1/assessments/ — Create
# ---------------------------------------------------------------------------


def _fetch_one(model, *filters):
    with TestingSessionLocal() as verify_db:
        return verify_db.query(model).filter(*filters).first()


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


def test_create_assessment_requires_available_credits_when_lemon_enabled(client, monkeypatch):
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers).json()

    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        assert org is not None
        org.credits_balance = 0
        db.commit()

    import app.components.assessments.service as assessments_svc

    monkeypatch.setattr(assessments_svc.settings, "MVP_DISABLE_LEMON", False)

    resp = create_assessment_via_api(client, headers, task["id"])
    assert resp.status_code == 402
    assert "purchase credits" in resp.json()["detail"].lower()


def test_create_assessment_blocks_when_pending_invites_already_reserve_remaining_credits(client, monkeypatch):
    headers, email = auth_headers(client)
    task = create_task_via_api(client, headers).json()

    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        assert org is not None
        org.credits_balance = 1
        db.commit()

    import app.components.assessments.service as assessments_svc

    monkeypatch.setattr(assessments_svc.settings, "MVP_DISABLE_LEMON", False)

    first = create_assessment_via_api(
        client,
        headers,
        task["id"],
        candidate_email="reserved-1@example.com",
        candidate_name="Reserved One",
    )
    assert first.status_code == 201, first.text

    second = create_assessment_via_api(
        client,
        headers,
        task["id"],
        candidate_email="reserved-2@example.com",
        candidate_name="Reserved Two",
    )
    assert second.status_code == 402
    assert "reserved for pending assessments" in second.json()["detail"].lower()


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
    canonical_task = Task(
        organization_id=None,
        name="AWS Glue Pipeline Recovery",
        description="Canonical demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=15,
        starter_code="print('demo')",
        test_code="def test_placeholder():\n    assert True\n",
        task_key="data_eng_aws_glue_pipeline_recovery",
    )
    db.add(canonical_task)
    db.commit()
    db.refresh(canonical_task)

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
        "assessment_track": "data_eng_aws_glue_pipeline_recovery",
        "marketing_consent": True,
    }
    resp = client.post("/api/v1/assessments/demo/start", json=payload)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["assessment_id"] > 0
    assert body["token"]
    assert body["sandbox_id"] == "sandbox-demo"

    demo_org = _fetch_one(Organization, Organization.slug == "taali-demo")
    assert demo_org is not None

    candidate = _fetch_one(Candidate, Candidate.email == "demo-user@example.com")
    assert candidate is not None
    assert candidate.organization_id == demo_org.id
    assert candidate.work_email == "demo-user@company.com"
    assert candidate.company_name == "Acme Corp"
    assert candidate.company_size == "51-200"
    assert candidate.lead_source == "landing_demo"
    assert candidate.marketing_consent is True

    assessment = _fetch_one(Assessment, Assessment.id == body["assessment_id"])
    assert assessment is not None
    assert assessment.is_demo is True
    assert assessment.demo_track == "data_eng_aws_glue_pipeline_recovery"
    assert assessment.task_id == canonical_task.id
    assert assessment.demo_profile["work_email"] == "demo-user@company.com"
    assert assessment.demo_profile["marketing_consent"] is True
    assert body["task"]["task_key"] == "data_eng_aws_glue_pipeline_recovery"


def test_demo_start_uses_selected_track_task(client, db, monkeypatch):
    platform_task = Task(
        organization_id=None,
        name="AWS Glue Pipeline Recovery",
        description="Data platform demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=30,
        starter_code="print('demo')",
        test_code="",
        task_key="data_eng_aws_glue_pipeline_recovery",
    )
    ai_task = Task(
        organization_id=None,
        name="GenAI Production Readiness Review",
        description="AI engineer demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=30,
        starter_code="print('demo')",
        test_code="",
        task_key="ai_eng_genai_production_readiness",
    )
    db.add(platform_task)
    db.add(ai_task)
    db.commit()
    db.refresh(platform_task)
    db.refresh(ai_task)

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

    # Legacy track "data_eng_c_backfill_schema" aliases to the canonical Glue task.
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
    assessment = _fetch_one(Assessment, Assessment.id == body["assessment_id"])
    assert assessment is not None
    assert assessment.demo_track == "data_eng_c_backfill_schema"
    assert assessment.task_id == platform_task.id
    assert body["task"]["task_key"] == "data_eng_aws_glue_pipeline_recovery"


def test_demo_start_falls_back_to_local_repo_when_branch_init_fails(client, db, monkeypatch):
    demo_task = Task(
        organization_id=None,
        name="AWS Glue Pipeline Recovery",
        description="Data platform demo task",
        task_type="python",
        difficulty="medium",
        duration_minutes=30,
        starter_code="print('demo')",
        test_code="",
        task_key="data_eng_aws_glue_pipeline_recovery",
        repo_structure={"files": {"src/main.py": "def run():\n    return 1\n"}},
    )
    db.add(demo_task)
    db.commit()
    db.refresh(demo_task)

    import app.components.assessments.service as assessments_svc

    holder = {}

    class FakeFiles:
        def __init__(self):
            self.writes = []

        def write(self, path, content):
            self.writes.append((path, content))

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = FakeFiles()
            self.run_code_calls = []

        def run_code(self, code):
            self.run_code_calls.append(code)
            if "'success': proc.returncode == 0" in code:
                return {"stdout": '{"success": true, "stderr": ""}\n', "stderr": "", "error": None}
            return {"stdout": "", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            sandbox = FakeSandbox("demo-fallback-sandbox")
            holder["sandbox"] = sandbox
            return sandbox

        def connect_sandbox(self, sandbox_id):
            return holder.get("sandbox") or FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, sandbox):
            return None

    class FailingRepoService:
        def __init__(self, github_org=None, github_token=None):
            self.github_org = github_org
            self.github_token = github_token

        def create_template_repo(self, task_obj):
            return None

        def create_assessment_branch(self, task_obj, assessment_id):
            raise RuntimeError("repo provisioning unavailable")

    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "AssessmentRepositoryService", FailingRepoService)
    monkeypatch.setattr(assessments_svc, "resolve_ai_mode", lambda: "claude_cli_terminal")
    monkeypatch.setattr(
        assessments_svc,
        "terminal_capabilities",
        lambda: {"enabled": True, "ws_protocol": "v1", "permission_mode": "default", "command": "claude", "active_mode": "claude_cli_terminal"},
    )

    payload = {
        "full_name": "Demo User",
        "position": "Engineering Manager",
        "email": "demo-fallback@example.com",
        "work_email": "demo-fallback@company.com",
        "company_name": "Acme Corp",
        "company_size": "51-200",
        "assessment_track": "data_eng_aws_glue_pipeline_recovery",
        "marketing_consent": True,
    }
    resp = client.post("/api/v1/assessments/demo/start", json=payload)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["sandbox_id"] == "demo-fallback-sandbox"
    assert body["task"]["task_key"] == "data_eng_aws_glue_pipeline_recovery"

    sandbox = holder["sandbox"]
    assert any(path.endswith("/src/main.py") for path, _ in sandbox.files.writes)
    assert any("'git', 'init', '-b', 'candidate'" in code for code in sandbox.run_code_calls)

    assessment = _fetch_one(Assessment, Assessment.id == body["assessment_id"])
    assert assessment is not None
    assert assessment.is_demo is True
    assert assessment.assessment_branch is None


def test_demo_start_accepts_legacy_track_keys(client, db, monkeypatch):
    platform_task = Task(
        organization_id=None,
        name="AWS Glue Pipeline Recovery",
        description="Demo task (legacy alias backend-reliability)",
        task_type="python",
        difficulty="medium",
        duration_minutes=30,
        starter_code="print('demo')",
        test_code="",
        task_key="data_eng_aws_glue_pipeline_recovery",
    )
    db.add(platform_task)
    db.commit()
    db.refresh(platform_task)

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
    assessment = _fetch_one(Assessment, Assessment.id == body["assessment_id"])
    assert assessment is not None
    assert assessment.demo_track == "backend-reliability"
    assert assessment.task_id == platform_task.id
    assert body["task"]["task_key"] == "data_eng_aws_glue_pipeline_recovery"


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


def test_finalize_candidate_feedback_and_fetch_public_report(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    assessment_token = env["assessment"]["token"]

    with TestingSessionLocal() as db:
        assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
        assert assessment is not None
        assessment.status = AssessmentStatus.COMPLETED
        assessment.score = 7.4
        assessment.started_at = datetime.now(timezone.utc)
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.score_breakdown = {
            "category_scores": {
                "task_completion": 7.8,
                "prompt_clarity": 8.1,
                "context_provision": 4.2,
                "independence_efficiency": 7.6,
                "response_utilization": 7.0,
                "debugging_design": 7.3,
                "written_communication": 5.1,
                "role_fit": 6.8,
            },
        }
        assessment.ai_prompts = [
            {
                "message": "Write a function for X. Context: service Y and caller Z. Return JSON and do not modify interface.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
        db.commit()

    finalize_resp = client.post(
        f"/api/v1/assessments/{assessment_id}/finalize-candidate-feedback",
        json={},
        headers=env["headers"],
    )
    assert finalize_resp.status_code == 200, finalize_resp.text
    finalize_payload = finalize_resp.json()
    assert finalize_payload["feedback_ready"] is True
    assert isinstance(finalize_payload.get("feedback"), dict)

    public_resp = client.get(f"/api/v1/assessments/{assessment_token}/feedback")
    assert public_resp.status_code == 200, public_resp.text
    public_payload = public_resp.json()
    assert public_payload["feedback_ready"] is True
    assert isinstance(public_payload["feedback"].get("dimensions"), list)
    assert public_payload["feedback"].get("overall_score") is not None

    pdf_resp = client.get(f"/api/v1/assessments/{assessment_token}/feedback.pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"].startswith("application/pdf")
    assert pdf_resp.content.startswith(b"%PDF")


def test_recruiter_report_pdf_is_client_facing_and_wrapped(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]

    with TestingSessionLocal() as db:
        assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
        assert assessment is not None
        assessment.status = AssessmentStatus.COMPLETED
        assessment.score = 7.4
        assessment.assessment_score = 74.0
        assessment.final_score = 74.0
        assessment.taali_score = 78.0
        assessment.started_at = datetime.now(timezone.utc)
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.total_duration_seconds = 2640
        assessment.total_prompts = 5
        assessment.tests_passed = 8
        assessment.tests_total = 10
        assessment.ai_prompts = [
            {
                "message": (
                    "Please review the failing ingestion worker. Context: the batch jobs run behind a scheduler, "
                    "must preserve the external interface, and should return structured JSON after the fix."
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
        assessment.cv_job_match_score = 84.0
        assessment.cv_job_match_details = {
            "summary": "Strong platform and data engineering background with clear production ownership.",
            "requirements_match_score_100": 80.0,
            "requirements_coverage": {
                "total": 3,
                "met": 2,
                "partially_met": 1,
                "missing": 0,
            },
            "matching_skills": ["Python", "Airflow", "AWS"],
            "experience_highlights": [
                "Led production migration programs with measurable delivery ownership.",
            ],
            "concerns": ["Needs deeper AWS Glue migration detail."],
            "requirements_assessment": [
                {
                    "requirement": "Production-grade data pipelines",
                    "status": "met",
                    "evidence": "Candidate has led production batch systems and incident response.",
                },
                {
                    "requirement": "AWS Glue migration depth",
                    "status": "partially_met",
                    "evidence": "Adjacent AWS delivery is clear, but direct Glue migration examples are thinner.",
                },
            ],
        }
        assessment.score_breakdown = {
            "score_formula_version": "taali_v3_role_fit_blended",
            "category_scores": {
                "task_completion": 7.8,
                "prompt_clarity": 8.1,
                "context_provision": 6.4,
                "independence_efficiency": 7.6,
                "response_utilization": 7.0,
                "debugging_design": 7.3,
                "written_communication": 8.2,
                "role_fit": 8.0,
            },
            "score_components": {
                "assessment_score": 74.0,
                "taali_score": 78.0,
                "role_fit_score": 82.0,
                "role_fit_components": {
                    "cv_fit_score": 84.0,
                    "requirements_fit_score": 80.0,
                },
            },
        }
        db.commit()

    resp = client.get(f"/api/v1/assessments/{assessment_id}/report.pdf", headers=env["headers"])
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "taali-client-report" in resp.headers["content-disposition"]

    reader = PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) == 1
    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "TAALI" in extracted_text
    assert "Client Assessment Summary" in extracted_text
    assert "TAALI score" in extracted_text
    assert "Role fit" in extracted_text
    assert "Assessment" in extracted_text
    assert "Role fit summary" in extracted_text
    assert "What to probe" in extracted_text
    assert "Strong platform and data" in extracted_text
    assert "engineering background" in extracted_text
    assert "Score model" not in extracted_text
    assert "CV fit" not in extracted_text
    assert "Requirements fit" not in extracted_text


def test_report_benchmark_filter_avoids_timeout_enum_literal():
    compiled = (
        select(Assessment.id)
        .where(_completed_assessment_query_filter())
        .compile(dialect=postgresql.dialect())
    )
    params = {key: str(value) for key, value in compiled.params.items()}

    assert "COMPLETED_DUE_TO_TIMEOUT" not in " ".join(params.values())
    assert any("completed" in value.lower() for value in params.values())


def test_public_feedback_rejected_before_finalize(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]
    assessment_token = env["assessment"]["token"]

    with TestingSessionLocal() as db:
        assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
        assert assessment is not None
        assessment.status = AssessmentStatus.COMPLETED
        assessment.score = 6.9
        db.commit()

    public_resp = client.get(f"/api/v1/assessments/{assessment_token}/feedback")
    assert public_resp.status_code == 403
    assert "not ready" in public_resp.json()["detail"].lower()


def test_interview_debrief_generation_is_cached(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]

    with TestingSessionLocal() as db:
        assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
        assert assessment is not None
        assessment.status = AssessmentStatus.COMPLETED
        assessment.score = 7.1
        assessment.score_breakdown = {
            "category_scores": {
                "prompt_clarity": 8.0,
                "context_provision": 4.4,
                "independence_efficiency": 7.2,
                "written_communication": 5.0,
            },
        }
        db.commit()

    first = client.post(
        f"/api/v1/assessments/{assessment_id}/interview-debrief",
        json={},
        headers=env["headers"],
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["cached"] is False
    questions = first_payload["interview_debrief"].get("probing_questions", [])
    assert len(questions) >= 3

    second = client.post(
        f"/api/v1/assessments/{assessment_id}/interview-debrief",
        json={},
        headers=env["headers"],
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["cached"] is True
    assert second_payload["interview_debrief"] == first_payload["interview_debrief"]


def test_finalize_candidate_feedback_blocked_when_org_toggle_disabled(client):
    headers, _ = auth_headers(client)
    task = create_task_via_api(client, headers).json()
    assessment = create_assessment_via_api(client, headers, task["id"]).json()

    patch_org = client.patch(
        "/api/v1/organizations/me",
        json={"candidate_feedback_enabled": False},
        headers=headers,
    )
    assert patch_org.status_code == 200, patch_org.text

    with TestingSessionLocal() as db:
        row = db.query(Assessment).filter(Assessment.id == assessment["id"]).first()
        assert row is not None
        row.status = AssessmentStatus.COMPLETED
        row.score = 7.0
        db.commit()

    finalize_resp = client.post(
        f"/api/v1/assessments/{assessment['id']}/finalize-candidate-feedback",
        json={},
        headers=headers,
    )
    assert finalize_resp.status_code == 403
    assert "disabled" in finalize_resp.json()["detail"].lower()
