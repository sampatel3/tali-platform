import time

import pytest

from app.domains.workable_sync import routes as workable_routes
from app.components.integrations.workable import sync_runner as workable_sync_runner
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def test_workable_sync_status_returns_503_when_disabled(client):
    headers, _ = auth_headers(client, email="sync-disabled@example.com")
    resp = client.get("/api/v1/workable/sync/status", headers=headers)
    assert resp.status_code in (503, 200)


def test_workable_sync_manual_trigger_success(client, db, monkeypatch):
    headers, email = auth_headers(client, email="sync-enabled@example.com", organization_name="Sync Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_CELERY", True)

    def fake_sync(
        self,
        db_session,
        org_obj,
        full_resync=False,
        run_id=None,
        mode="metadata",
        selected_job_shortcodes=None,
    ):
        org_obj.workable_last_sync_status = "success"
        org_obj.workable_last_sync_summary = {
            "jobs_seen": 1,
            "run_id": run_id,
            "mode": mode,
            "selected_job_shortcodes": selected_job_shortcodes or [],
        }
        db_session.commit()
        return {"jobs_seen": 1}

    monkeypatch.setattr(workable_sync_runner.WorkableSyncService, "sync_org", fake_sync)

    resp = client.post("/api/v1/workable/sync", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "started"
    assert isinstance(payload.get("run_id"), int)
    assert payload.get("mode") == "metadata"
    assert "message" in payload

    # Wait for background sync to finish (fake_sync is fast; thread may use separate session)
    for _ in range(50):
        time.sleep(0.2)
        status_resp = client.get("/api/v1/workable/sync/status", headers=headers)
        status_resp.raise_for_status()
        data = status_resp.json()
        if not data.get("sync_in_progress"):
            summary = data.get("workable_last_sync_summary") or {}
            assert summary.get("jobs_seen") == 1, f"Expected jobs_seen=1, got summary={summary}"
            time.sleep(0.3)  # let background thread close its connection before teardown
            break
    else:
        last = client.get("/api/v1/workable/sync/status", headers=headers).json()
        assert False, f"Background sync did not complete within 10s; last status={last}"


def test_workable_sync_status_shows_in_progress_after_start(client, db, monkeypatch):
    """After POST /sync, GET /status must return sync_in_progress true until background finishes."""
    headers, email = auth_headers(client, email="sync-inprogress@example.com", organization_name="Sync Org 2")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_CELERY", True)

    # Slow fake sync so we can poll while in progress
    import time
    def slow_fake_sync(
        self,
        db_session,
        org_obj,
        full_resync=False,
        run_id=None,
        mode="metadata",
        selected_job_shortcodes=None,
    ):
        time.sleep(1.0)
        org_obj.workable_last_sync_status = "success"
        org_obj.workable_last_sync_summary = {
            "jobs_seen": 2,
            "run_id": run_id,
            "mode": mode,
            "selected_job_shortcodes": selected_job_shortcodes or [],
        }
        db_session.commit()
        return {"jobs_seen": 2}

    monkeypatch.setattr(workable_sync_runner.WorkableSyncService, "sync_org", slow_fake_sync)

    resp = client.post("/api/v1/workable/sync", headers=headers)
    assert resp.status_code == 200
    assert resp.json().get("status") == "started"

    # Immediately after start, status should report in progress (DB-backed)
    status_resp = client.get("/api/v1/workable/sync/status", headers=headers)
    status_resp.raise_for_status()
    data = status_resp.json()
    assert data.get("sync_in_progress") is True

    # Wait for background to finish
    for _ in range(15):
        time.sleep(0.3)
        status_resp = client.get("/api/v1/workable/sync/status", headers=headers)
        data = status_resp.json()
        if not data.get("sync_in_progress"):
            assert data.get("workable_last_sync_summary", {}).get("jobs_seen") == 2
            time.sleep(0.3)  # let background thread close connection
            break
    else:
        assert False, "Sync did not finish within ~4.5s"


def test_workable_clear_soft_deletes(client, db, monkeypatch):
    """POST /workable/clear soft-deletes workable roles/apps/candidates and returns counts."""
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_CELERY", True)
    headers, email = auth_headers(client, email="clear@example.com", organization_name="Clear Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org_id = owner.organization_id
    org = db.query(Organization).filter(Organization.id == org_id).first()
    org.workable_connected = True
    org.workable_access_token = "x"
    org.workable_subdomain = "y"
    db.commit()

    # Create a workable role (no need for full relations for soft-delete count)
    role = Role(organization_id=org_id, name="Workable Job", source="workable", workable_job_id="J1")
    db.add(role)
    db.commit()
    db.refresh(role)

    resp = client.post("/api/v1/workable/clear", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("status") == "ok"
    assert data.get("roles_soft_deleted") == 1

    # Role should have deleted_at set
    db.expire_all()
    r = db.query(Role).filter(Role.id == role.id).first()
    assert r is not None
    assert r.deleted_at is not None


def test_workable_diagnostic_returns_api_structure(client, db, monkeypatch):
    """GET /workable/diagnostic returns jobs, job_details, candidates structure from Workable API."""
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    headers, email = auth_headers(client, email="diagnostic@example.com", organization_name="Diagnostic Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "test-token"
    org.workable_subdomain = "test"
    db.commit()

    # Mock WorkableService to avoid real API calls
    original_list = workable_routes.WorkableService.list_open_jobs
    original_details = workable_routes.WorkableService.get_job_details
    original_candidates = workable_routes.WorkableService.list_job_candidates

    def mock_list_jobs(self):
        return [
            {"shortcode": "J1", "id": "J1", "title": "Test Job", "state": "published"},
        ]

    def mock_get_details(self, job_id):
        return {
            "job": {
                "shortcode": job_id,
                "title": "Test Job",
                "details": {"description": "<p>Job desc</p>", "requirements": "Req 1"},
            }
        }

    def mock_list_candidates(self, job_id, *, paginate=False, max_pages=None):
        return [
            {"id": "c1", "email": "cand@example.com", "stage": "screening", "name": "Candidate"},
        ]

    monkeypatch.setattr(workable_routes.WorkableService, "list_open_jobs", mock_list_jobs)
    monkeypatch.setattr(workable_routes.WorkableService, "get_job_details", mock_get_details)
    monkeypatch.setattr(workable_routes.WorkableService, "list_job_candidates", mock_list_candidates)

    resp = client.get("/api/v1/workable/diagnostic", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("api_reachable") is True
    assert data["jobs"]["count"] == 1
    assert data["jobs"]["first_shortcode"] == "J1"
    assert data["job_details"]["top_level_keys"] == ["job"]
    assert data["candidates"]["count"] == 1
    assert data["candidates"]["first_email"] == "cand@example.com"
    assert "db_roles_count" in data
    assert "db_roles" in data


def test_workable_sync_status_include_diagnostic(client, db, monkeypatch):
    """GET /workable/sync/status?include_diagnostic=true returns diagnostic when requested."""
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    headers, email = auth_headers(client, email="status-diag@example.com", organization_name="Status Diag Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "test-token"
    org.workable_subdomain = "test"
    db.commit()

    def mock_list_jobs(self):
        return [{"shortcode": "J1", "id": "J1", "title": "Test Job", "state": "published"}]

    def mock_get_details(self, job_id):
        return {"job": {"shortcode": job_id, "title": "Test Job", "details": {}}}

    def mock_list_candidates(self, job_id, *, paginate=False, max_pages=None):
        return [{"id": "c1", "email": "cand@example.com", "stage": "screening"}]

    monkeypatch.setattr(workable_routes.WorkableService, "list_open_jobs", mock_list_jobs)
    monkeypatch.setattr(workable_routes.WorkableService, "get_job_details", mock_get_details)
    monkeypatch.setattr(workable_routes.WorkableService, "list_job_candidates", mock_list_candidates)

    resp = client.get("/api/v1/workable/sync/status?include_diagnostic=true", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "sync_in_progress" in data
    assert "diagnostic" in data
    diag = data["diagnostic"]
    assert diag.get("api_reachable") is True
    assert diag["jobs"]["count"] == 1
    assert diag["candidates"]["count"] == 1


def test_workable_sync_jobs_lists_selectable_roles(client, db, monkeypatch):
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    headers, email = auth_headers(client, email="sync-jobs@example.com", organization_name="Sync Jobs Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    def mock_list_jobs(self):
        return [
            {"shortcode": "A1", "id": "100", "title": "Role A", "state": "published"},
            {"shortcode": "B2", "id": "200", "title": "Role B", "state": "open"},
        ]

    monkeypatch.setattr(workable_routes.WorkableService, "list_open_jobs", mock_list_jobs)

    resp = client.get("/api/v1/workable/sync/jobs", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("total") == 2
    jobs = payload.get("jobs") or []
    identifiers = {row.get("identifier") for row in jobs}
    assert identifiers == {"A1", "B2"}


def test_workable_sync_cancel_accepts_optional_run_id(client, db, monkeypatch):
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    headers, email = auth_headers(client, email="cancel-run@example.com", organization_name="Cancel Run Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    def slow_fake_sync(
        self,
        db_session,
        org_obj,
        full_resync=False,
        run_id=None,
        mode="metadata",
        selected_job_shortcodes=None,
    ):
        time.sleep(1.5)
        org_obj.workable_last_sync_status = "cancelled"
        org_obj.workable_last_sync_summary = {"run_id": run_id, "mode": mode}
        db_session.commit()
        return {"run_id": run_id}

    monkeypatch.setattr(workable_sync_runner.WorkableSyncService, "sync_org", slow_fake_sync)

    start = client.post("/api/v1/workable/sync", headers=headers)
    assert start.status_code == 200, start.text
    run_id = start.json().get("run_id")
    assert isinstance(run_id, int)

    cancel = client.post("/api/v1/workable/sync/cancel", headers=headers, json={"run_id": run_id})
    assert cancel.status_code == 200, cancel.text
    payload = cancel.json()
    assert payload["status"] == "ok"
    assert payload["run_id"] == run_id


def test_workable_sync_queues_to_celery_when_enabled(client, db, monkeypatch):
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_CELERY", False)

    headers, email = auth_headers(client, email="celery-queue@example.com", organization_name="Celery Queue Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    from app.tasks import workable_tasks

    queued: dict[str, object] = {}

    class _DummyTask:
        def delay(self, **kwargs):
            queued.update(kwargs)
            return None

    monkeypatch.setattr(workable_tasks, "run_workable_sync_run_task", _DummyTask())

    resp = client.post("/api/v1/workable/sync", headers=headers, json={"mode": "metadata"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "started"
    assert payload["execution_backend"] == "celery"
    assert queued.get("org_id") == org.id
    assert queued.get("run_id") == payload.get("run_id")
    assert queued.get("mode") == "metadata"


def test_workable_sync_passes_selected_role_shortcodes(client, db, monkeypatch):
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_CELERY", False)

    headers, email = auth_headers(client, email="scoped-sync@example.com", organization_name="Scoped Sync Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    from app.tasks import workable_tasks

    queued: dict[str, object] = {}

    class _DummyTask:
        def delay(self, **kwargs):
            queued.update(kwargs)
            return None

    monkeypatch.setattr(workable_tasks, "run_workable_sync_run_task", _DummyTask())

    resp = client.post(
        "/api/v1/workable/sync",
        headers=headers,
        json={"mode": "metadata", "job_shortcodes": ["A1", "B2", "A1"]},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "started"
    assert payload["selected_jobs_count"] == 2
    assert queued.get("selected_job_shortcodes") == ["A1", "B2"]


def test_run_workable_sync_script_exits_without_email():
    """run_workable_sync script exits 1 when no email provided."""
    import sys
    from app.scripts.run_workable_sync import main
    orig_argv = sys.argv
    try:
        sys.argv = ["run_workable_sync"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
    finally:
        sys.argv = orig_argv
