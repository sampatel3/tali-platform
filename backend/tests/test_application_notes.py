"""API tests for per-candidate recruiter notes.

Covers POST /api/v1/applications/{id}/notes for a candidate with NO
assessment linked (the legacy /assessments/{id}/notes path dead-ended
there): the note lands on the application event timeline, rides in the
agent-facing get_application payload when ``for_agent`` (the default), is
excludable from the agent view, validates empty input, and is org-scoped.
"""

from app.mcp.payloads import application_detail
from app.models.candidate_application import CandidateApplication
from tests.conftest import auth_headers


def _agent_recruiter_notes(db, application_id):
    """The recruiter notes as the agent sees them via get_application."""
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == application_id)
        .first()
    )
    return application_detail(app).get("recruiter_notes") or []


def _create_application(client, headers, candidate_email="notes@example.com"):
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


def test_add_note_without_assessment_lands_on_timeline_and_agent_payload(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers)
    aid = app["id"]

    resp = client.post(
        f"/api/v1/applications/{aid}/notes",
        headers=headers,
        json={"note": "Already interviewed elsewhere — not suitable."},
    )
    assert resp.status_code == 200, resp.text
    event = resp.json()
    assert event["event_type"] == "recruiter_note"
    assert event["actor_type"] == "recruiter"
    assert event["metadata"]["note"] == "Already interviewed elsewhere — not suitable."
    assert event["metadata"]["for_agent"] is True

    # Shows on the application event timeline (the Notes tab reads this).
    events = client.get(f"/api/v1/applications/{aid}/events", headers=headers)
    assert events.status_code == 200, events.text
    notes = [e for e in events.json() if e["event_type"] == "recruiter_note"]
    assert len(notes) == 1
    assert notes[0]["reason"] == "Already interviewed elsewhere — not suitable."

    # Rides in the agent-facing get_application payload as standing guidance.
    agent_notes = _agent_recruiter_notes(db, aid)
    assert any("not suitable" in n["note"] for n in agent_notes)


def test_note_can_be_hidden_from_agent(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="team-only@example.com")
    aid = app["id"]

    resp = client.post(
        f"/api/v1/applications/{aid}/notes",
        headers=headers,
        json={"note": "Called him, no answer — will retry.", "for_agent": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["metadata"]["for_agent"] is False

    # Still on the human timeline...
    events = client.get(f"/api/v1/applications/{aid}/events", headers=headers)
    assert any(e["event_type"] == "recruiter_note" for e in events.json())

    # ...but excluded from what the agent reads.
    assert _agent_recruiter_notes(db, aid) == []


def test_add_note_rejects_empty(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="empty@example.com")
    # Whitespace-only passes Pydantic's min_length=1 but the service strips it
    # to empty and returns a 400.
    resp = client.post(
        f"/api/v1/applications/{app['id']}/notes",
        headers=headers,
        json={"note": "   "},
    )
    assert resp.status_code == 400, resp.text
    # A truly empty string is rejected by schema validation (422).
    resp_empty = client.post(
        f"/api/v1/applications/{app['id']}/notes",
        headers=headers,
        json={"note": ""},
    )
    assert resp_empty.status_code == 422, resp_empty.text


def test_add_note_is_org_scoped(client):
    headers_a, _ = auth_headers(client)
    headers_b, _ = auth_headers(client)
    app = _create_application(client, headers_a, candidate_email="scoped@example.com")

    forbidden = client.post(
        f"/api/v1/applications/{app['id']}/notes",
        headers=headers_b,
        json={"note": "should not work"},
    )
    assert forbidden.status_code == 404, forbidden.text
