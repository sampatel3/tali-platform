"""API tests for per-candidate recruiter notes.

Covers POST /api/v1/applications/{id}/notes for a candidate with NO
assessment linked (the legacy /assessments/{id}/notes path dead-ended
there): the note lands on the application event timeline, rides in the
agent-facing get_application payload when ``for_agent`` (the default), is
excludable from the agent view, validates empty input, and is org-scoped.
"""

from app.mcp.payloads import application_detail
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
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


def _create_related_role(
    db,
    *,
    app: dict,
    name: str,
    include_application: bool,
) -> Role:
    owner = db.get(Role, int(app["role_id"]))
    related = Role(
        organization_id=int(owner.organization_id),
        name=name,
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        related_source_role_id=int(owner.id),
        ats_owner_role_id=int(owner.id),
        job_spec_text=(
            "Independent related role with a complete specification for "
            "production engineering and reliable delivery."
        ),
    )
    db.add(related)
    db.flush()
    if include_application:
        db.add(
            SisterRoleEvaluation(
                organization_id=int(owner.organization_id),
                role_id=int(related.id),
                candidate_id=int(app["candidate_id"]),
                source_application_id=int(app["id"]),
                ats_application_id=int(app["id"]),
                status="done",
                pipeline_stage="review",
                application_outcome="open",
                membership_source="initial_snapshot",
                spec_fingerprint=f"application-note-{related.id}",
                role_fit_score=84,
            )
        )
    db.commit()
    return related


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


def test_add_ranking_note_stores_kind_and_rides_in_agent_payload(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="ranking@example.com")
    aid = app["id"]

    resp = client.post(
        f"/api/v1/applications/{aid}/notes",
        headers=headers,
        json={"note": "Solid but light on system design.", "kind": "ranking", "ranking": 4},
    )
    assert resp.status_code == 200, resp.text
    event = resp.json()
    assert event["event_type"] == "recruiter_note"
    assert event["metadata"]["kind"] == "ranking"
    assert event["metadata"]["ranking"] == 4
    assert event["metadata"]["note"] == "Solid but light on system design."

    # The agent reads a readable "Ranking: 4/5 — …" form.
    agent_notes = _agent_recruiter_notes(db, aid)
    assert any(
        "Ranking: 4/5" in n["note"] and "system design" in n["note"] for n in agent_notes
    )


def test_add_link_note_stores_url_label_and_rides_in_agent_payload(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="link@example.com")
    aid = app["id"]

    resp = client.post(
        f"/api/v1/applications/{aid}/notes",
        headers=headers,
        json={
            "note": "Portfolio",
            "kind": "link",
            "link_url": "https://example.com/portfolio",
            "link_label": "Portfolio",
        },
    )
    assert resp.status_code == 200, resp.text
    event = resp.json()
    assert event["metadata"]["kind"] == "link"
    assert event["metadata"]["link_url"] == "https://example.com/portfolio"
    assert event["metadata"]["link_label"] == "Portfolio"

    # The agent reads a readable "Link: <label> <url>" form.
    agent_notes = _agent_recruiter_notes(db, aid)
    assert any(
        "Link:" in n["note"]
        and "Portfolio" in n["note"]
        and "https://example.com/portfolio" in n["note"]
        for n in agent_notes
    )


def test_ranking_note_out_of_range_rejected(client):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="range@example.com")
    # ranking must be 1–5 (schema ge=1, le=5).
    resp = client.post(
        f"/api/v1/applications/{app['id']}/notes",
        headers=headers,
        json={"note": "great", "kind": "ranking", "ranking": 9},
    )
    assert resp.status_code == 422, resp.text


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


def test_related_role_note_stays_on_related_timeline_and_agent_payload(client, db):
    from app.mcp.handlers import get_role_candidate

    headers, email = auth_headers(client)
    app = _create_application(
        client,
        headers,
        candidate_email="related-note@example.com",
    )
    related = _create_related_role(
        db,
        app=app,
        name="Related note role",
        include_application=True,
    )
    user = db.query(User).filter(User.email == email).one()

    response = client.post(
        f"/api/v1/applications/{app['id']}/notes",
        headers=headers,
        json={
            "note": "Use the related-role interview rubric.",
            "role_id": int(related.id),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["role_id"] == int(related.id)
    related_events = client.get(
        f"/api/v1/applications/{app['id']}/events",
        headers=headers,
        params={"role_id": int(related.id)},
    )
    owner_events = client.get(
        f"/api/v1/applications/{app['id']}/events",
        headers=headers,
    )
    assert any(
        event["event_type"] == "recruiter_note"
        for event in related_events.json()
    )
    assert all(
        event["event_type"] != "recruiter_note"
        for event in owner_events.json()
    )

    related_payload = get_role_candidate(
        db,
        user,
        role_id=int(related.id),
        application_id=int(app["id"]),
    )
    owner_payload = get_role_candidate(
        db,
        user,
        role_id=int(app["role_id"]),
        application_id=int(app["id"]),
    )
    assert any(
        "related-role interview rubric" in note["note"]
        for note in related_payload["recruiter_notes"]
    )
    assert owner_payload["recruiter_notes"] == []


def test_add_note_rejects_cross_role_spoof_and_missing_related_membership(client, db):
    headers, _ = auth_headers(client)
    app = _create_application(
        client,
        headers,
        candidate_email="role-spoof-note@example.com",
    )
    owner = db.get(Role, int(app["role_id"]))
    unrelated = Role(
        organization_id=int(owner.organization_id),
        name="Unrelated ordinary role",
        source="manual",
    )
    db.add(unrelated)
    db.commit()
    empty_related = _create_related_role(
        db,
        app=app,
        name="Related role without membership",
        include_application=False,
    )

    for spoofed_role_id in (int(unrelated.id), int(empty_related.id)):
        response = client.post(
            f"/api/v1/applications/{app['id']}/notes",
            headers=headers,
            json={
                "note": "This must not cross role boundaries.",
                "role_id": spoofed_role_id,
            },
        )
        assert response.status_code == 404, response.text

    owner_events = client.get(
        f"/api/v1/applications/{app['id']}/events",
        headers=headers,
    )
    assert all(
        event["event_type"] != "recruiter_note"
        for event in owner_events.json()
    )
