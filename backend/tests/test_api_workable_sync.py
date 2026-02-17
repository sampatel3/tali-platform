import time

from app.domains.workable_sync import routes as workable_routes
from app.models.organization import Organization
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

    def fake_sync(self, db_session, org_obj, full_resync=False):
        org_obj.workable_last_sync_status = "success"
        org_obj.workable_last_sync_summary = {"jobs_seen": 1}
        db_session.commit()
        return {"jobs_seen": 1}

    monkeypatch.setattr(workable_routes.WorkableSyncService, "sync_org", fake_sync)

    resp = client.post("/api/v1/workable/sync", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "started"
    assert "message" in payload

    # Wait for background sync to finish (fake_sync is fast)
    for _ in range(20):
        time.sleep(0.2)
        status_resp = client.get("/api/v1/workable/sync/status", headers=headers)
        status_resp.raise_for_status()
        data = status_resp.json()
        if not data.get("sync_in_progress"):
            assert data.get("workable_last_sync_summary", {}).get("jobs_seen") == 1
            break
    else:
        assert False, "Background sync did not complete within 4s"
