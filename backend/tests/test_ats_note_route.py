"""Recruiter ATS-note request identity and synchronous validation."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.models.background_job_run import BackgroundJobRun
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.user import User
from app.platform.config import settings
from app.services.background_job_runs import update_run
from tests.conftest import auth_headers


def _workable_application(client, db):
    headers, email = auth_headers(
        client,
        email="ats-note-route@example.com",
        organization_name="ATS Note Route Org",
    )
    role_response = client.post(
        "/api/v1/roles",
        headers=headers,
        json={"name": "Note role", "description": "A live note role"},
    )
    assert role_response.status_code == 201, role_response.text
    application_response = client.post(
        f"/api/v1/roles/{role_response.json()['id']}/applications",
        headers=headers,
        json={
            "candidate_email": "ats-note-candidate@example.com",
            "candidate_name": "ATS Note Candidate",
        },
    )
    assert application_response.status_code == 201, application_response.text
    user = db.query(User).filter(User.email == email).one()
    org = db.query(Organization).filter(Organization.id == user.organization_id).one()
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == application_response.json()["id"])
        .one()
    )
    org.workable_connected = True
    org.workable_access_token = "workable-route-token"
    org.workable_subdomain = "ats-note-route"
    org.workable_config = {
        "workable_writeback": True,
        "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
        "workable_actor_member_id": "member-route",
    }
    app.workable_candidate_id = "workable-note-route-candidate"
    db.commit()
    return headers, user, app


def test_recruiter_note_requires_explicit_request_key_and_nonblank_body(
    client, db, monkeypatch
):
    headers, _user, app = _workable_application(client, db)
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    path = f"/api/v1/applications/{app.id}/workable/note"

    missing_key = client.post(path, headers=headers, json={"body": "A real note"})
    whitespace = client.post(
        path,
        headers={**headers, "Idempotency-Key": "whitespace-note"},
        json={"body": "   \n\t  "},
    )

    assert missing_key.status_code == 422, missing_key.text
    assert whitespace.status_code == 422, whitespace.text
    assert db.query(BackgroundJobRun).count() == 0


def test_recruiter_note_disabled_gate_creates_no_job_or_broker_publish(
    client, db, monkeypatch
):
    headers, _user, app = _workable_application(client, db)
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", True)

    with patch(
        "app.tasks.workable_tasks.run_workable_op_task.apply_async"
    ) as publish:
        response = client.post(
            f"/api/v1/applications/{app.id}/workable/note",
            headers={**headers, "Idempotency-Key": "disabled-note"},
            json={"body": "This must not be persisted or published."},
        )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "WORKABLE_INTEGRATION_DISABLED"
    publish.assert_not_called()
    assert db.query(BackgroundJobRun).count() == 0


@pytest.mark.parametrize(
    "missing_authority",
    [
        "connection",
        "access_token",
        "subdomain",
        "writeback",
        "write_scope",
        "actor_member",
    ],
)
def test_recruiter_note_rejects_unusable_workable_authority_before_queue(
    client, db, monkeypatch, missing_authority
):
    headers, user, app = _workable_application(client, db)
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org = db.query(Organization).filter(Organization.id == user.organization_id).one()
    config = dict(org.workable_config or {})
    if missing_authority == "connection":
        org.workable_connected = False
    elif missing_authority == "access_token":
        org.workable_access_token = None
    elif missing_authority == "subdomain":
        org.workable_subdomain = None
    elif missing_authority == "writeback":
        config["workable_writeback"] = False
    elif missing_authority == "write_scope":
        config["granted_scopes"] = ["r_jobs", "r_candidates"]
    elif missing_authority == "actor_member":
        config.pop("workable_actor_member_id", None)
    org.workable_config = config
    db.commit()

    with patch(
        "app.tasks.workable_tasks.run_workable_op_task.apply_async"
    ) as publish:
        response = client.post(
            f"/api/v1/applications/{app.id}/workable/note",
            headers={
                **headers,
                "Idempotency-Key": f"missing-{missing_authority}",
            },
            json={"body": "Do not queue an impossible provider write."},
        )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ATS_NOTE_PROVIDER_UNAVAILABLE"
    publish.assert_not_called()
    assert db.query(BackgroundJobRun).count() == 0


def test_recruiter_note_retry_reuses_exact_intent_but_preserves_new_intents(
    client, db, monkeypatch
):
    headers, user, app = _workable_application(client, db)
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    path = f"/api/v1/applications/{app.id}/workable/note"
    retry_headers = {**headers, "Idempotency-Key": "recruiter-request-1"}

    with patch(
        "app.tasks.workable_tasks.run_workable_op_task.apply_async"
    ) as publish:
        first = client.post(
            path,
            headers=retry_headers,
            json={"body": "  Preserve this normalized note.  "},
        )
        replay = client.post(
            path,
            headers=retry_headers,
            json={"body": "Preserve this normalized note."},
        )
        second_intent = client.post(
            path,
            headers={**headers, "Idempotency-Key": "recruiter-request-2"},
            json={"body": "Preserve this normalized note."},
        )
        conflict = client.post(
            path,
            headers=retry_headers,
            json={"body": "A different note must not borrow that request key."},
        )
        assert update_run(
            first.json()["job_run_id"],
            status="completed",
            counters={"op_type": "post_note", "status": "ok"},
            finished=True,
        )
        completed_replay = client.post(
            path,
            headers=retry_headers,
            json={"body": "Preserve this normalized note."},
        )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert second_intent.status_code == 200, second_intent.text
    assert first.json()["job_run_id"] == replay.json()["job_run_id"]
    assert second_intent.json()["job_run_id"] != first.json()["job_run_id"]
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "ATS_NOTE_IDEMPOTENCY_CONFLICT"
    assert completed_replay.status_code == 200, completed_replay.text
    assert completed_replay.json()["job_run_id"] == first.json()["job_run_id"]
    assert publish.call_count == 2

    db.expire_all()
    rows = db.query(BackgroundJobRun).order_by(BackgroundJobRun.id).all()
    assert len(rows) == 2
    for row in rows:
        counters = dict(row.counters or {})
        assert counters["note_body_sha256"]
        assert counters["note_intent_sha256"]
        assert "Preserve this normalized note." not in json.dumps(counters)
        assert "recruiter-request" not in str(row.dispatch_key)
        assert f"/{int(user.organization_id)}/{int(user.id)}/{int(app.id)}/" in str(
            row.dispatch_key
        )
