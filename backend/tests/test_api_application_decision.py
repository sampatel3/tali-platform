"""API tests for the logical-role application manual decision endpoint.

Covers recording/updating a recruiter decision for a candidate with NO
assessment linked (e.g. rejected at CV stage): PATCH
/api/v1/applications/{id}/manual-decision, the draft→submitted lifecycle,
optimistic locking, validation, org scoping, and the GET round-trip.
"""

from datetime import datetime, timezone

from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from tests.conftest import auth_headers


def _create_application(client, headers, candidate_email="no-assessment@example.com"):
    role = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer", "description": "Hiring"},
        headers=headers,
    )
    assert role.status_code == 201, role.text
    role = role.json()
    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": candidate_email, "candidate_name": "No Assessment"},
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    return app_resp.json()


def test_application_manual_decision_records_and_updates(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers)
    aid = app["id"]
    # Freshly created application has no decision yet.
    assert app.get("manual_decision") is None

    # Draft (working state).
    draft = client.patch(
        f"/api/v1/applications/{aid}/manual-decision",
        headers=headers,
        json={
            "status": "draft",
            "decision": "hold",
            "rationale": "Need more info before deciding.",
            "confidence": "low",
            "next_steps": ["Request references"],
        },
    )
    assert draft.status_code == 200, draft.text
    d = draft.json()["manual_decision"]
    assert d["status"] == "draft"
    assert d["version"] == 1
    assert d["decision"] == "hold"
    assert d["next_steps"] == ["Request references"]
    assert d["updated_by"]["name"]
    assert d["submitted_at"] is None
    assert [h["action"] for h in d["history"]] == ["saved_draft"]

    # Submit (recorded decision), echoing the loaded version.
    submit = client.patch(
        f"/api/v1/applications/{aid}/manual-decision",
        headers=headers,
        json={
            "status": "submitted",
            "expected_version": 1,
            "decision": "reject",
            "rationale": "Below the bar on core requirements.",
            "confidence": "high",
        },
    )
    assert submit.status_code == 200, submit.text
    s = submit.json()["manual_decision"]
    assert s["status"] == "submitted"
    assert s["version"] == 2
    assert s["decision"] == "reject"
    assert s["submitted_at"] is not None
    assert [h["action"] for h in s["history"]] == ["saved_draft", "submitted"]

    # GET round-trips the decision through the application detail payload.
    detail = client.get(f"/api/v1/applications/{aid}", headers=headers)
    assert detail.status_code == 200, detail.text
    md = detail.json()["manual_decision"]
    assert md["decision"] == "reject"
    assert md["status"] == "submitted"
    assert md["version"] == 2

    # A submitted decision can be re-opened and updated.
    update = client.patch(
        f"/api/v1/applications/{aid}/manual-decision",
        headers=headers,
        json={
            "status": "submitted",
            "expected_version": 2,
            "decision": "advance",
            "rationale": "Reconsidered after recruiter screen.",
            "confidence": "medium",
        },
    )
    assert update.status_code == 200, update.text
    u = update.json()["manual_decision"]
    assert u["version"] == 3
    assert u["decision"] == "advance"
    assert u["history"][-1]["action"] == "updated"


def test_application_manual_decision_optimistic_lock_returns_409(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers)
    aid = app["id"]

    first = client.patch(
        f"/api/v1/applications/{aid}/manual-decision",
        headers=headers,
        json={"status": "submitted", "decision": "advance", "rationale": "Go", "confidence": "high"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1

    # Stale editor still thinks it's at version 0 → conflict.
    stale = client.patch(
        f"/api/v1/applications/{aid}/manual-decision",
        headers=headers,
        json={"expected_version": 0, "decision": "reject", "rationale": "No", "confidence": "low"},
    )
    assert stale.status_code == 409, stale.text
    assert "updated by someone else" in stale.json()["detail"].lower()


def test_application_manual_decision_rejects_invalid_decision(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers)
    aid = app["id"]
    resp = client.patch(
        f"/api/v1/applications/{aid}/manual-decision",
        headers=headers,
        json={"decision": "strong_maybe", "rationale": "x"},
    )
    assert resp.status_code == 400, resp.text
    assert "decision must be one of" in resp.json()["detail"]


def test_application_manual_decision_is_org_scoped(client):
    headers_a, _ = auth_headers(client)
    headers_b, _ = auth_headers(client)
    app = _create_application(client, headers_a)
    aid = app["id"]

    # A different org cannot record a decision against this application.
    forbidden = client.patch(
        f"/api/v1/applications/{aid}/manual-decision",
        headers=headers_b,
        json={"decision": "advance", "rationale": "x", "confidence": "low"},
    )
    assert forbidden.status_code == 404, forbidden.text


def test_owner_and_related_manual_decisions_are_independent(client, db):
    headers, _ = auth_headers(client)
    app_payload = _create_application(
        client,
        headers,
        candidate_email="independent-decisions@example.com",
    )
    application = db.get(CandidateApplication, int(app_payload["id"]))
    owner = db.get(Role, int(application.role_id))
    related = Role(
        organization_id=int(application.organization_id),
        name="Independent decision role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        related_source_role_id=int(owner.id),
        ats_owner_role_id=int(owner.id),
        job_spec_text="Independent logical role decision specification",
    )
    db.add(related)
    db.flush()
    membership = SisterRoleEvaluation(
        organization_id=int(application.organization_id),
        role_id=int(related.id),
        candidate_id=int(application.candidate_id),
        source_application_id=int(application.id),
        ats_application_id=int(application.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="independent-manual-decision",
        role_fit_score=84,
    )
    db.add(membership)
    db.commit()

    owner_update = client.patch(
        f"/api/v1/applications/{application.id}/manual-decision",
        headers=headers,
        json={
            "status": "submitted",
            "decision": "advance",
            "rationale": "Owner-role evidence supports advancing.",
            "confidence": "high",
        },
    )
    related_update = client.patch(
        f"/api/v1/applications/{application.id}/manual-decision",
        params={"view_role_id": int(related.id)},
        headers=headers,
        json={
            "status": "submitted",
            "decision": "reject",
            "rationale": "Related-role requirements are not met.",
            "confidence": "high",
        },
    )
    assert owner_update.status_code == 200, owner_update.text
    assert related_update.status_code == 200, related_update.text

    owner_detail = client.get(
        f"/api/v1/applications/{application.id}",
        headers=headers,
    )
    related_detail = client.get(
        f"/api/v1/applications/{application.id}",
        params={"view_role_id": int(related.id)},
        headers=headers,
    )
    assert owner_detail.status_code == 200, owner_detail.text
    assert related_detail.status_code == 200, related_detail.text
    assert owner_detail.json()["manual_decision"]["decision"] == "advance"
    assert related_detail.json()["manual_decision"]["decision"] == "reject"

    db.expire_all()
    application = db.get(CandidateApplication, int(application.id))
    membership = db.get(SisterRoleEvaluation, int(membership.id))
    assert application.manual_decision["decision"] == "advance"
    assert membership.manual_decision["decision"] == "reject"
    event_role_ids = {
        int(role_id)
        for (role_id,) in db.query(CandidateApplicationEvent.role_id)
        .filter(
            CandidateApplicationEvent.application_id == int(application.id),
            CandidateApplicationEvent.event_type == "manual_decision_recorded",
        )
        .all()
    }
    assert event_role_ids == {int(owner.id), int(related.id)}


def test_related_timeline_uses_historical_membership_authority(client, db):
    headers, _ = auth_headers(client)
    app_payload = _create_application(
        client,
        headers,
        candidate_email="historical-related-timeline@example.com",
    )
    application = db.get(CandidateApplication, int(app_payload["id"]))
    owner = db.get(Role, int(application.role_id))
    related = Role(
        organization_id=int(application.organization_id),
        name="Historical timeline role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        related_source_role_id=int(owner.id),
        ats_owner_role_id=int(owner.id),
        job_spec_text="Independent timeline role",
    )
    unrelated = Role(
        organization_id=int(application.organization_id),
        name="Unrelated role",
        source="manual",
    )
    db.add_all([related, unrelated])
    db.flush()
    membership = SisterRoleEvaluation(
        organization_id=int(application.organization_id),
        role_id=int(related.id),
        candidate_id=int(application.candidate_id),
        source_application_id=int(application.id),
        ats_application_id=int(application.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="historical-timeline",
        role_fit_score=61,
    )
    db.add(membership)
    db.commit()

    recorded = client.patch(
        f"/api/v1/applications/{application.id}/manual-decision",
        params={"view_role_id": int(related.id)},
        headers=headers,
        json={
            "status": "submitted",
            "decision": "hold",
            "rationale": "Keep this role-local audit marker.",
            "confidence": "medium",
        },
    )
    assert recorded.status_code == 200, recorded.text

    # An unrelated role cannot borrow the source application's identity to read
    # another role's event stream.
    unrelated_history = client.get(
        f"/api/v1/applications/{application.id}/events",
        params={"role_id": int(unrelated.id)},
        headers=headers,
    )
    assert unrelated_history.status_code == 404, unrelated_history.text

    # Source/evidence deletion does not remove a live independent membership.
    application.deleted_at = datetime.now(timezone.utc)
    db.commit()
    live_history = client.get(
        f"/api/v1/applications/{application.id}/events",
        params={"role_id": int(related.id)},
        headers=headers,
    )
    assert live_history.status_code == 200, live_history.text
    assert "manual_decision_recorded" in {
        event["event_type"] for event in live_history.json()
    }
    live_detail = client.get(
        f"/api/v1/applications/{application.id}",
        params={"view_role_id": int(related.id)},
        headers=headers,
    )
    assert live_detail.status_code == 200, live_detail.text

    # Removing membership closes current-state access, while its immutable,
    # role-attributed audit events remain readable to an authorized role viewer.
    membership.deleted_at = datetime.now(timezone.utc)
    db.commit()
    historical = client.get(
        f"/api/v1/applications/{application.id}/events",
        params={"role_id": int(related.id)},
        headers=headers,
    )
    assert historical.status_code == 200, historical.text
    removed_detail = client.get(
        f"/api/v1/applications/{application.id}",
        params={"view_role_id": int(related.id)},
        headers=headers,
    )
    assert removed_detail.status_code == 404, removed_detail.text
