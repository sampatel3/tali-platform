"""
QA Test Suite: Tasks CRUD & Validation
Covers: create, list, get, update, delete, validation, auth, edge cases.
~35 tests
"""
from tests.conftest import verify_user


def _auth_headers(client, email="u@example.com"):
    client.post("/api/v1/auth/register", json={
        "email": email, "password": "ValidPass1!", "full_name": "Test User", "organization_name": "TestOrg",
    })
    verify_user(email)
    token = client.post("/api/v1/auth/jwt/login", data={"username": email, "password": "ValidPass1!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


VALID_TASK = {
    "name": "Test Task",
    "description": "A test task description here",
    "task_type": "debugging",
    "difficulty": "mid",
    "duration_minutes": 30,
    "starter_code": "print('hello')",
    "test_code": "assert True",
}


# ===========================================================================
# A. CREATE TASK
# ===========================================================================
class TestCreateTask:
    def test_create_success(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/tasks", json=VALID_TASK, headers=h)
        assert r.status_code == 201
        d = r.json()
        assert d["name"] == "Test Task"
        assert d["task_type"] == "debugging"
        assert d["difficulty"] == "mid"
        assert d["duration_minutes"] == 30
        assert d["is_active"] is True
        assert "id" in d
        assert "created_at" in d

    def test_create_with_all_optional_fields(self, client):
        h = _auth_headers(client)
        body = {**VALID_TASK, "sample_data": {"key": "val"}, "dependencies": ["numpy"],
                "success_criteria": {"tests": True}, "is_template": False,
                "score_weights": {"task_completion": 0.5}, "proctoring_enabled": True,
                "task_key": "ai_eng_a_prompt_cache", "role": "ai_engineer",
                "scenario": "Fix flaky cache invalidation in a service.",
                "repo_structure": {"files": {"src/service.py": "def run():\n    return 1"}},
                "evaluation_rubric": {"correctness": 0.7, "quality": 0.3},
                "extra_data": {"hints": ["look at cache key"]}}
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 201
        d = r.json()
        assert d["task_key"] == "ai_eng_a_prompt_cache"
        assert d["repo_structure"]["files"]["src/service.py"].startswith("def run")


    def test_create_accepts_task_id_alias_and_top_level_insights(self, client):
        h = _auth_headers(client)
        body = {
            **VALID_TASK,
            "task_id": "ai_eng_a_prompt_cache",
            "expected_insights": ["lots of repeated prompts"],
            "valid_solutions": ["redis exact-match cache"],
            "extra_data": {"difficulty_notes": "prefer pragmatic fix"},
        }
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 201
        d = r.json()
        assert d["task_key"] == "ai_eng_a_prompt_cache"
        assert d["extra_data"]["difficulty_notes"] == "prefer pragmatic fix"
        assert d["extra_data"]["expected_insights"] == ["lots of repeated prompts"]
        assert d["extra_data"]["valid_solutions"] == ["redis exact-match cache"]


    def test_create_recreates_main_repo_snapshot(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path))
        h = _auth_headers(client)
        body = {
            **VALID_TASK,
            "task_key": "llm-cache",
            "name": "LLM Cache Task",
            "repo_structure": {"files": {"src/app.py": "print('ok')"}},
        }
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 201

        repo_root_entries = list(tmp_path.iterdir())
        assert repo_root_entries, "expected task repo to be created"
        repo_dir = repo_root_entries[0]
        assert (repo_dir / "src" / "app.py").exists()
        assert (repo_dir / ".git").exists()

    def test_create_no_auth(self, client):
        r = client.post("/api/v1/tasks", json=VALID_TASK)
        assert r.status_code == 401

    def test_create_missing_name(self, client):
        h = _auth_headers(client)
        body = {k: v for k, v in VALID_TASK.items() if k != "name"}
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_name_too_short(self, client):
        h = _auth_headers(client)
        body = {**VALID_TASK, "name": "ab"}  # min 3
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_name_too_long(self, client):
        h = _auth_headers(client)
        body = {**VALID_TASK, "name": "A" * 201}  # max 200
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_duration_below_min(self, client):
        h = _auth_headers(client)
        body = {**VALID_TASK, "duration_minutes": 10}  # min 15
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_duration_above_max(self, client):
        h = _auth_headers(client)
        body = {**VALID_TASK, "duration_minutes": 200}  # max 180
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_missing_description(self, client):
        h = _auth_headers(client)
        body = {k: v for k, v in VALID_TASK.items() if k != "description"}
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_missing_starter_code(self, client):
        h = _auth_headers(client)
        body = {k: v for k, v in VALID_TASK.items() if k != "starter_code"}
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_missing_test_code(self, client):
        h = _auth_headers(client)
        body = {k: v for k, v in VALID_TASK.items() if k != "test_code"}
        r = client.post("/api/v1/tasks", json=body, headers=h)
        assert r.status_code == 422

    def test_create_empty_body(self, client):
        h = _auth_headers(client)
        r = client.post("/api/v1/tasks", json={}, headers=h)
        assert r.status_code == 422


# ===========================================================================
# B. LIST TASKS
# ===========================================================================
class TestListTasks:
    def test_list_empty(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/tasks", headers=h)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_after_create(self, client):
        h = _auth_headers(client)
        client.post("/api/v1/tasks", json=VALID_TASK, headers=h)
        r = client.get("/api/v1/tasks", headers=h)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_list_no_auth(self, client):
        r = client.get("/api/v1/tasks")
        assert r.status_code == 401


# ===========================================================================
# C. GET TASK
# ===========================================================================
class TestGetTask:
    def test_get_success(self, client):
        h = _auth_headers(client)
        tid = client.post("/api/v1/tasks", json=VALID_TASK, headers=h).json()["id"]
        r = client.get(f"/api/v1/tasks/{tid}", headers=h)
        assert r.status_code == 200
        assert r.json()["id"] == tid

    def test_get_nonexistent(self, client):
        h = _auth_headers(client)
        r = client.get("/api/v1/tasks/99999", headers=h)
        assert r.status_code == 404

    def test_get_no_auth(self, client):
        r = client.get("/api/v1/tasks/1")
        assert r.status_code == 401


# ===========================================================================
# D. UPDATE TASK
# ===========================================================================
class TestUpdateTask:
    def test_update_name(self, client):
        h = _auth_headers(client)
        tid = client.post("/api/v1/tasks", json=VALID_TASK, headers=h).json()["id"]
        r = client.patch(f"/api/v1/tasks/{tid}", json={"name": "Updated Name"}, headers=h)
        assert r.status_code == 200
        assert r.json()["name"] == "Updated Name"

    def test_update_duration(self, client):
        h = _auth_headers(client)
        tid = client.post("/api/v1/tasks", json=VALID_TASK, headers=h).json()["id"]
        r = client.patch(f"/api/v1/tasks/{tid}", json={"duration_minutes": 60}, headers=h)
        assert r.status_code == 200
        assert r.json()["duration_minutes"] == 60

    def test_update_nonexistent(self, client):
        h = _auth_headers(client)
        r = client.patch("/api/v1/tasks/99999", json={"name": "Valid Name Here"}, headers=h)
        assert r.status_code in [404, 422]

    def test_update_no_auth(self, client):
        r = client.patch("/api/v1/tasks/1", json={"name": "X"})
        assert r.status_code == 401


# ===========================================================================
# E. DELETE TASK
# ===========================================================================
class TestDeleteTask:
    def test_delete_success(self, client):
        h = _auth_headers(client)
        tid = client.post("/api/v1/tasks", json=VALID_TASK, headers=h).json()["id"]
        r = client.delete(f"/api/v1/tasks/{tid}", headers=h)
        assert r.status_code == 204

    def test_delete_nonexistent(self, client):
        h = _auth_headers(client)
        r = client.delete("/api/v1/tasks/99999", headers=h)
        assert r.status_code == 404

    def test_delete_no_auth(self, client):
        r = client.delete("/api/v1/tasks/1")
        assert r.status_code == 401

    def test_delete_task_used_by_assessment_fails(self, client):
        h = _auth_headers(client)
        tid = client.post("/api/v1/tasks", json=VALID_TASK, headers=h).json()["id"]
        # Create assessment referencing this task
        client.post("/api/v1/assessments", json={
            "candidate_email": "c@e.com", "candidate_name": "C", "task_id": tid,
        }, headers=h)
        r = client.delete(f"/api/v1/tasks/{tid}", headers=h)
        # Should fail because task is referenced
        assert r.status_code in [400, 409, 500]  # depending on implementation

    def test_get_after_delete_returns_404(self, client):
        h = _auth_headers(client)
        tid = client.post("/api/v1/tasks", json=VALID_TASK, headers=h).json()["id"]
        client.delete(f"/api/v1/tasks/{tid}", headers=h)
        r = client.get(f"/api/v1/tasks/{tid}", headers=h)
        assert r.status_code == 404
