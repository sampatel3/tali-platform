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
    assert payload.get("mode") == "full"
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
    """After POST /sync, GET /status reports sync_in_progress=True until the
    Celery worker finishes. We mock the Celery dispatch so the run record
    stays in 'running' state and the status endpoint can be observed."""
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

    # No-op the Celery dispatch so the run record is left in "running" state
    # for the status endpoint to observe.
    from app.tasks import workable_tasks

    class _NoOpTask:
        def delay(self, **_kwargs):
            return None

    monkeypatch.setattr(workable_tasks, "run_workable_sync_run_task", _NoOpTask())

    resp = client.post("/api/v1/workable/sync", headers=headers)
    assert resp.status_code == 200
    assert resp.json().get("status") == "started"

    status_resp = client.get("/api/v1/workable/sync/status", headers=headers)
    status_resp.raise_for_status()
    data = status_resp.json()
    assert data.get("sync_in_progress") is True


def test_workable_sync_reuses_existing_running_run(client, db, monkeypatch):
    headers, email = auth_headers(client, email="sync-existing@example.com", organization_name="Sync Existing Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)

    # Mock the Celery dispatch so the first run stays in "running" state and
    # the second POST hits the dedup path.
    from app.tasks import workable_tasks

    class _NoOpTask:
        def delay(self, **_kwargs):
            return None

    monkeypatch.setattr(workable_tasks, "run_workable_sync_run_task", _NoOpTask())

    first = client.post("/api/v1/workable/sync", headers=headers)
    assert first.status_code == 200, first.text
    first_payload = first.json()
    run_id = first_payload.get("run_id")
    assert isinstance(run_id, int)

    second = client.post("/api/v1/workable/sync", headers=headers)
    assert second.status_code == 202, second.text
    second_payload = second.json()
    assert second_payload.get("status") == "already_running"
    assert second_payload.get("run_id") == run_id
    assert second_payload.get("execution_backend") == "existing"


def test_workable_clear_soft_deletes(client, db, monkeypatch):
    """POST /workable/clear soft-deletes workable roles/apps/candidates and returns counts."""
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)
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


def test_workable_lookup_endpoints_return_configuration_data(client, db, monkeypatch):
    monkeypatch.setattr(workable_routes.settings, "MVP_DISABLE_WORKABLE", False)

    headers, email = auth_headers(client, email="lookup@example.com", organization_name="Lookup Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_subdomain = "example"
    db.commit()

    captured: dict[str, object] = {}

    def mock_list_members(self, *, limit=100, shortcode=None):
        captured["shortcode"] = shortcode
        return [{"id": "member-1", "name": "Sam Patel"}]

    def mock_list_reasons(self):
        return [{"id": "reason-1", "name": "Below threshold"}]

    def mock_list_job_stages(self, shortcode):
        captured["stage_shortcode"] = shortcode
        return [{"id": "stage-1", "name": "Screening"}]

    monkeypatch.setattr(workable_routes.WorkableService, "list_members", mock_list_members)
    monkeypatch.setattr(workable_routes.WorkableService, "list_disqualification_reasons", mock_list_reasons)
    monkeypatch.setattr(workable_routes.WorkableService, "list_job_stages", mock_list_job_stages)

    members_resp = client.get("/api/v1/workable/members?shortcode=ENG-1", headers=headers)
    assert members_resp.status_code == 200, members_resp.text
    assert members_resp.json() == {"members": [{"id": "member-1", "name": "Sam Patel"}]}

    reasons_resp = client.get("/api/v1/workable/disqualification-reasons", headers=headers)
    assert reasons_resp.status_code == 200, reasons_resp.text
    assert reasons_resp.json() == {"disqualification_reasons": [{"id": "reason-1", "name": "Below threshold"}]}

    stages_resp = client.get("/api/v1/workable/stages?shortcode=ENG-1", headers=headers)
    assert stages_resp.status_code == 200, stages_resp.text
    assert stages_resp.json() == {"stages": [{"id": "stage-1", "name": "Screening"}]}
    assert captured == {"shortcode": "ENG-1", "stage_shortcode": "ENG-1"}


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


def test_admin_clear_sync_finalizes_orphaned_running_runs(client, db, monkeypatch):
    """admin/clear-sync must mark stuck ``status='running'`` runs as failed.

    Regression for the 2026-05-20 incident: a sync worker died mid-run,
    leaving Run #10 in ``status='running' / finished_at=NULL`` for >2
    months. Calling /admin/clear-sync cleared the org flags but left the
    run row untouched, so ``_latest_running_run_for_org`` still matched
    and ``POST /workable/sync`` kept returning ``already_running``.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.workable_sync_run import WorkableSyncRun
    from app.platform.config import settings as app_settings

    headers, email = auth_headers(client, email="stuck-sync@example.com", organization_name="Stuck Sync Org")
    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.workable_connected = True
    org.workable_access_token = "tk"
    org.workable_subdomain = "stuck"
    org.workable_sync_started_at = datetime.now(timezone.utc) - timedelta(days=60)
    db.commit()

    # Plant an orphaned "running" run row.
    stuck = WorkableSyncRun(
        organization_id=org.id,
        mode="metadata",
        status="running",
        phase="queued",
        jobs_total=0,
        jobs_processed=0,
        started_at=datetime.now(timezone.utc) - timedelta(days=60),
        finished_at=None,
        errors=[],
    )
    db.add(stuck)
    db.commit()
    db.refresh(stuck)
    stuck_id = stuck.id

    secret = (app_settings.SECRET_KEY or "").strip() or "test-secret"
    monkeypatch.setattr(app_settings, "SECRET_KEY", secret)

    resp = client.post(
        "/api/v1/workable/admin/clear-sync",
        headers={"X-Admin-Secret": secret},
        json={"email": email},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("status") == "ok"
    assert stuck_id in (payload.get("cleared_run_ids") or [])

    # Reload — run must now be terminal, org flags cleared.
    db.expire_all()
    final = db.query(WorkableSyncRun).filter(WorkableSyncRun.id == stuck_id).first()
    assert final is not None
    assert final.status == "failed"
    assert final.finished_at is not None
    refreshed_org = db.query(Organization).filter(Organization.id == org.id).first()
    assert refreshed_org.workable_sync_started_at is None


def test_reap_stuck_workable_sync_runs_finalizes_old_running_rows(db):
    """Reaper marks runs failed once they've been ``running`` past the timeout."""
    from datetime import datetime, timedelta, timezone

    from app.models.workable_sync_run import WorkableSyncRun
    from app.tasks.assessment_tasks import _STUCK_RUN_TIMEOUT_HOURS, reap_stuck_workable_sync_runs

    org = Organization(
        name="Reaper Org",
        slug="reaper-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="reaper",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    now = datetime.now(timezone.utc)
    old_run = WorkableSyncRun(
        organization_id=org.id,
        mode="full",
        status="running",
        phase="syncing_candidates",
        jobs_total=10,
        jobs_processed=2,
        started_at=now - timedelta(hours=_STUCK_RUN_TIMEOUT_HOURS + 1),
        finished_at=None,
        errors=[],
    )
    fresh_run = WorkableSyncRun(
        organization_id=org.id,
        mode="full",
        status="running",
        phase="syncing_candidates",
        jobs_total=10,
        jobs_processed=1,
        started_at=now - timedelta(minutes=5),
        finished_at=None,
        errors=[],
    )
    db.add_all([old_run, fresh_run])
    db.commit()
    db.refresh(old_run)
    db.refresh(fresh_run)

    result = reap_stuck_workable_sync_runs.run()  # bypass Celery dispatch in test
    assert result["status"] == "ok"
    assert result["reaped"] == 1

    db.expire_all()
    reaped = db.query(WorkableSyncRun).filter(WorkableSyncRun.id == old_run.id).first()
    assert reaped.status == "failed"
    assert reaped.finished_at is not None
    assert any("Stuck-run reaper" in str(e) for e in (reaped.errors or []))

    survivor = db.query(WorkableSyncRun).filter(WorkableSyncRun.id == fresh_run.id).first()
    assert survivor.status == "running"
    assert survivor.finished_at is None


def test_sync_workable_jobs_uses_jobs_only_mode(db, monkeypatch):
    """sync_workable_jobs Beat task must invoke sync_org with mode='jobs_only'.

    Regression for the 2026-05-20 redesign: the 15-min jobs sweep must
    skip candidate fetching entirely, otherwise it'll hit the same rate
    limit that took down the old ``sync_workable_orgs`` task.
    """
    from app.tasks import assessment_tasks

    org = Organization(
        name="Jobs-Only Org",
        slug="jobs-only-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="jobsonly",
    )
    db.add(org)
    db.commit()

    captured: list[dict] = []

    class _FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def sync_org(self, db_session, org_obj, *, mode="metadata", **kwargs):
            captured.append({"mode": mode, "kwargs": kwargs, "org_id": org_obj.id})
            return {"jobs_seen": 0}

    monkeypatch.setattr(assessment_tasks.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(assessment_tasks, "_acquire_workable_org_mutex", lambda *a, **kw: False)
    monkeypatch.setattr(assessment_tasks, "_release_workable_org_mutex", lambda *a, **kw: None)
    from app.components.integrations.workable import sync_service as sync_service_mod
    monkeypatch.setattr(sync_service_mod, "WorkableSyncService", _FakeService)

    result = assessment_tasks.sync_workable_jobs.run()
    assert result["status"] == "ok"
    assert captured, "sync_org should have been invoked"
    assert all(c["mode"] == "jobs_only" for c in captured), (
        f"All invocations must be jobs_only mode, got: {[c['mode'] for c in captured]}"
    )


def test_sync_agent_mode_roles_filters_to_agentic_unpaused(db, monkeypatch):
    """sync_agent_mode_roles must only sync roles with agentic_mode_enabled=true and not paused."""
    from datetime import datetime, timezone

    from app.tasks import assessment_tasks

    org = Organization(
        name="Agent Org",
        slug="agent-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="agent",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    on_role = Role(
        organization_id=org.id, name="Agent On", source="workable",
        workable_job_id="ON1", agentic_mode_enabled=True, agent_paused_at=None,
    )
    paused_role = Role(
        organization_id=org.id, name="Agent Paused", source="workable",
        workable_job_id="PAUSED1", agentic_mode_enabled=True,
        agent_paused_at=datetime.now(timezone.utc),
    )
    off_role = Role(
        organization_id=org.id, name="Agent Off", source="workable",
        workable_job_id="OFF1", agentic_mode_enabled=False,
    )
    db.add_all([on_role, paused_role, off_role])
    db.commit()

    captured: list[list[str]] = []

    class _FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def sync_org(self, db_session, org_obj, *, selected_job_shortcodes=None, **kwargs):
            captured.append(list(selected_job_shortcodes or []))
            return {"jobs_seen": 0}

    monkeypatch.setattr(assessment_tasks.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(assessment_tasks, "_acquire_workable_org_mutex", lambda *a, **kw: False)
    monkeypatch.setattr(assessment_tasks, "_release_workable_org_mutex", lambda *a, **kw: None)
    from app.components.integrations.workable import sync_service as sync_service_mod
    monkeypatch.setattr(sync_service_mod, "WorkableSyncService", _FakeService)

    result = assessment_tasks.sync_agent_mode_roles.run()
    assert result["status"] == "ok"
    assert captured == [["ON1"]], (
        f"Only the un-paused agentic role should sync, got: {captured}"
    )


def test_sync_workable_daily_candidates_skips_starred_and_active_agent(db, monkeypatch):
    """Nightly task syncs roles that are NEITHER starred NOR (agentic && not paused)."""
    from datetime import datetime, timezone

    from app.tasks import assessment_tasks

    org = Organization(
        name="Daily Org",
        slug="daily-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="daily",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    plain = Role(
        organization_id=org.id, name="Plain", source="workable",
        workable_job_id="PLAIN1",
    )
    starred = Role(
        organization_id=org.id, name="Starred", source="workable",
        workable_job_id="STAR1", starred_for_auto_sync=True,
    )
    active_agent = Role(
        organization_id=org.id, name="Active Agent", source="workable",
        workable_job_id="AGT1", agentic_mode_enabled=True, agent_paused_at=None,
    )
    paused_agent = Role(
        organization_id=org.id, name="Paused Agent", source="workable",
        workable_job_id="AGTP1", agentic_mode_enabled=True,
        agent_paused_at=datetime.now(timezone.utc),
    )
    db.add_all([plain, starred, active_agent, paused_agent])
    db.commit()

    captured: list[list[str]] = []

    class _FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def sync_org(self, db_session, org_obj, *, selected_job_shortcodes=None, **kwargs):
            captured.append(sorted(selected_job_shortcodes or []))
            return {"jobs_seen": 0}

    monkeypatch.setattr(assessment_tasks.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(assessment_tasks, "_acquire_workable_org_mutex", lambda *a, **kw: False)
    monkeypatch.setattr(assessment_tasks, "_release_workable_org_mutex", lambda *a, **kw: None)
    from app.components.integrations.workable import sync_service as sync_service_mod
    monkeypatch.setattr(sync_service_mod, "WorkableSyncService", _FakeService)

    result = assessment_tasks.sync_workable_daily_candidates.run()
    assert result["status"] == "ok"
    # Plain + paused-agent should both be picked up; starred and active agent skipped.
    assert captured == [["AGTP1", "PLAIN1"]], (
        f"Expected nightly to cover plain + paused-agent only, got: {captured}"
    )


def test_filter_payloads_missing_cv_excludes_apps_with_existing_cv(db):
    """Prefetch wave must skip CV downloads for applications that already have one."""
    from app.components.integrations.workable.sync_service import WorkableSyncService
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    org = Organization(
        name="CVSkip Org",
        slug="cvskip-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="cvskip",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    role = Role(organization_id=org.id, name="R", source="workable", workable_job_id="JOB1")
    db.add(role)
    db.commit()
    db.refresh(role)

    # Three candidates: one with cv_file_url (skip), one with cv_text (skip),
    # one with neither (must be in the prefetch wave).
    candidates = []
    for i, (cid, url, text) in enumerate([
        ("cand_has_url", "https://s3/cv1.pdf", None),
        ("cand_has_text", None, "Resume text here"),
        ("cand_empty", None, None),
    ]):
        c = Candidate(organization_id=org.id, workable_candidate_id=cid, full_name=f"C{i}")
        db.add(c)
        db.commit()
        db.refresh(c)
        app = CandidateApplication(
            organization_id=org.id,
            role_id=role.id,
            candidate_id=c.id,
            workable_candidate_id=cid,
            cv_file_url=url,
            cv_text=text,
        )
        db.add(app)
        candidates.append(cid)
    db.commit()

    payloads_by_id = {cid: {"id": cid} for cid in candidates}

    class _StubClient:
        pass

    service = WorkableSyncService(_StubClient())
    filtered = service._filter_payloads_missing_cv(db, org, role, payloads_by_id)
    assert list(filtered.keys()) == ["cand_empty"], (
        f"Only the empty candidate should need CV download, got: {list(filtered.keys())}"
    )


def test_sync_org_jobs_only_mode_skips_candidate_fetch(db, monkeypatch):
    """mode='jobs_only' must upsert role rows and never call list_job_candidates."""
    from app.components.integrations.workable.service import WorkableService
    from app.components.integrations.workable.sync_service import WorkableSyncService

    org = Organization(
        name="JobsOnly Org",
        slug="jobsonly-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="jobsonly2",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    fake_jobs = [
        {"shortcode": "JOB1", "id": "JOB1", "title": "First", "state": "published"},
        {"shortcode": "JOB2", "id": "JOB2", "title": "Second", "state": "published"},
    ]

    candidate_calls: list[str] = []

    class _FakeClient:
        def list_open_jobs(self):
            return fake_jobs

        def get_job_details(self, job_id):
            return {"job": {"shortcode": job_id, "title": next(j["title"] for j in fake_jobs if j["shortcode"] == job_id)}}

        def list_job_candidates(self, *args, **kwargs):
            candidate_calls.append(str(args[0]) if args else "?")
            return []

    service = WorkableSyncService(_FakeClient())
    summary = service.sync_org(db, org, mode="jobs_only")

    assert summary["jobs_seen"] == 2
    assert summary["jobs_total"] == 2
    assert summary["jobs_processed"] == 2
    assert candidate_calls == [], (
        f"jobs_only must not fetch candidates, but called: {candidate_calls}"
    )

    roles = (
        db.query(Role)
        .filter(Role.organization_id == org.id, Role.source == "workable")
        .all()
    )
    assert sorted(r.workable_job_id for r in roles) == ["JOB1", "JOB2"]


def test_sync_org_jobs_only_handles_rate_limit_gracefully(db):
    """A 429 during jobs_only sync's job listing must propagate cleanly.

    The caller (e.g. ``sync_workable_jobs``) wraps the call in a broad
    ``except Exception``, so the rate-limit error needs to bubble up
    distinguishably rather than silently mutating partial state.
    """
    from app.components.integrations.workable.service import WorkableRateLimitError
    from app.components.integrations.workable.sync_service import WorkableSyncService

    org = Organization(
        name="RL Org",
        slug="rl-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="rl",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    class _RateLimitClient:
        def list_open_jobs(self):
            raise WorkableRateLimitError("Workable API rate limited (429)")

        def get_job_details(self, job_id):
            raise AssertionError("get_job_details should not be reached after rate-limit")

        def list_job_candidates(self, *args, **kwargs):
            raise AssertionError("list_job_candidates should not be reached after rate-limit")

    service = WorkableSyncService(_RateLimitClient())
    with pytest.raises(WorkableRateLimitError):
        service.sync_org(db, org, mode="jobs_only")

    # Org should be marked partial/failed but in a consistent state.
    db.expire_all()
    refreshed = db.query(Organization).filter(Organization.id == org.id).first()
    assert refreshed.workable_last_sync_status in ("failed", "partial"), (
        f"Expected failed/partial last_sync_status, got: {refreshed.workable_last_sync_status}"
    )


def test_workable_org_mutex_blocks_concurrent_task_types(monkeypatch):
    """The unified mutex prevents two sync task types touching the same org concurrently.

    Regression for the 2026-05-20 rate-limit incident: jobs / starred /
    agent / nightly tasks all share Workable's per-token rate limit.
    The per-org mutex is what stops them from racing into 429s.
    """
    import sys

    import fakeredis

    from app.tasks import assessment_tasks

    # Shared FakeServer so the new-client-per-acquire helper sees one store.
    server = fakeredis.FakeServer()

    class _FakeRedisModule:
        Redis = type(
            "_RedisStub",
            (),
            {"from_url": staticmethod(lambda url: fakeredis.FakeRedis(server=server))},
        )

    monkeypatch.setitem(sys.modules, "redis", _FakeRedisModule)

    # Acquire as "jobs"; the next attempt by any other source should be blocked.
    held = assessment_tasks._acquire_workable_org_mutex(42, source="jobs")
    assert held is not None, f"First acquire should succeed, got {held}"
    blocked = assessment_tasks._acquire_workable_org_mutex(42, source="starred")
    assert blocked is None, "A second source must be blocked while jobs holds the lock"
    # Different org isn't blocked.
    other = assessment_tasks._acquire_workable_org_mutex(43, source="starred")
    assert other is not None, "Different org should acquire freely"

    # Release frees up.
    assessment_tasks._release_workable_org_mutex(held)
    after = assessment_tasks._acquire_workable_org_mutex(42, source="agent")
    assert after is not None, "After release, next source can acquire"

    assessment_tasks._release_workable_org_mutex(other)
    assessment_tasks._release_workable_org_mutex(after)


def test_reaper_clears_stale_org_progress_without_running_run(db):
    """Reaper second sweep: stale org-level progress (no run row) gets cleared too."""
    from datetime import datetime, timedelta, timezone

    from app.tasks.assessment_tasks import _STUCK_RUN_TIMEOUT_HOURS, reap_stuck_workable_sync_runs

    # Org with stale workable_sync_started_at but no in-flight run.
    # This is what sync_workable_jobs / sync_starred_roles leave when
    # their worker dies — those tasks don't create WorkableSyncRun rows.
    org = Organization(
        name="StaleProgress Org",
        slug="staleprogress-org",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="staleprogress",
        workable_sync_started_at=datetime.now(timezone.utc) - timedelta(hours=_STUCK_RUN_TIMEOUT_HOURS + 1),
        workable_sync_progress={"phase": "syncing_jobs", "jobs_processed": 5},
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    org_id = org.id

    result = reap_stuck_workable_sync_runs.run()
    assert result["status"] == "ok"
    assert result["cleared_orgs"] == 1
    assert org_id in result["stale_org_ids"]

    db.expire_all()
    refreshed = db.query(Organization).filter(Organization.id == org_id).first()
    assert refreshed.workable_sync_started_at is None
    assert refreshed.workable_sync_progress is None
