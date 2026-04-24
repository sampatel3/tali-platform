from app.models.organization import Organization
from app.models.user import User
from tests.conftest import auth_headers


def test_disconnect_workable_clears_tokens_and_resets_mode(client, db):
    headers, email = auth_headers(client, email="workable-owner@example.com", organization_name="Workable Org")

    owner = db.query(User).filter(User.email == email).first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None

    org.workable_connected = True
    org.workable_access_token = "token"
    org.workable_refresh_token = "refresh"
    org.workable_subdomain = "deeplight"
    org.workable_last_sync_status = "success"
    org.workable_last_sync_summary = {"new_candidates": 3}
    org.workable_config = {
        "workflow_mode": "workable_hybrid",
        "email_mode": "workable_preferred_fallback_manual",
        "score_precedence": "workable_first",
        "sync_interval_minutes": 30,
        "invite_stage_name": "Taali assessment",
        "sync_model": "scheduled_pull_only",
        "sync_scope": "open_jobs_active_candidates",
    }
    db.commit()

    resp = client.delete("/api/v1/organizations/workable", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    db.refresh(org)
    assert org.workable_connected is False
    assert org.workable_access_token is None
    assert org.workable_refresh_token is None
    assert org.workable_subdomain is None
    assert org.workable_last_sync_status is None
    assert org.workable_last_sync_summary is None
    assert org.workable_config["workflow_mode"] == "manual"
    assert org.workable_config["email_mode"] == "manual_taali"
