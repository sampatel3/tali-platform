"""API tests for the application-level manual decision endpoint.

Covers recording/updating a recruiter decision for a candidate with NO
assessment linked (e.g. rejected at CV stage): PATCH
/api/v1/applications/{id}/manual-decision, the draft→submitted lifecycle,
optimistic locking, validation, org scoping, and the GET round-trip.
"""

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
