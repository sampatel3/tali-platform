"""API tests for the multi-link share contract (HANDOFF v2 §3).

Covers:
- POST  /api/v1/applications/{id}/share-links → create with mode + expiry
- GET   /api/v1/applications/{id}/share-links → list
- DELETE /api/v1/share-links/{id} → revoke
- GET   /share/{token} → public view, gated by expiry + view count
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.share_link import ShareLink
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers, TestingSessionLocal


def _make_role_and_application(client, headers, candidate_email="share-link@example.com"):
    role_resp = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer"},
        headers=headers,
    )
    assert role_resp.status_code == 201, role_resp.text
    role = role_resp.json()
    # The create-application route requires a job spec on file before
    # accepting applications, so upload a placeholder spec first.
    job_spec_file = {
        "file": ("job-spec.txt", io.BytesIO(b"Backend role requirements"), "text/plain"),
    }
    spec_resp = client.post(
        f"/api/v1/roles/{role['id']}/upload-job-spec",
        files=job_spec_file,
        headers=headers,
    )
    assert spec_resp.status_code == 200, spec_resp.text
    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={
            "candidate_email": candidate_email,
            "candidate_name": "Share Link",
            "candidate_position": "Engineer",
        },
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    return role, app_resp.json()


def _make_related_share_membership(db, *, user_email: str):
    user = db.query(User).filter(User.email == user_email).one()
    owner = Role(
        organization_id=int(user.organization_id),
        name="Physical ATS Owner",
        source="workable",
        workable_job_id=f"SHARE-OWNER-{id(db)}",
        job_spec_text="Owner role specification.",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=int(user.organization_id),
        name="Logical Reliability Role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        related_source_role_id=int(owner.id),
        ats_owner_role_id=int(owner.id),
        job_spec_text="Related reliability engineering specification.",
    )
    db.add(related)
    db.flush()
    candidate = Candidate(
        organization_id=int(user.organization_id),
        email=f"related-share-{id(db)}@example.com",
        full_name="Related Share Candidate",
        cv_text="Python reliability and production incident evidence.",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(user.organization_id),
        candidate_id=int(candidate.id),
        role_id=int(owner.id),
        status="advanced",
        pipeline_stage="advanced",
        pipeline_stage_source="sync",
        application_outcome="rejected",
        source="workable",
        cv_text=candidate.cv_text,
        taali_score_cache_100=12,
        rank_score=13,
        pre_screen_score_100=14,
        requirements_fit_score_100=15,
        cv_match_score=16,
        cv_match_details={"summary": "Physical owner evidence"},
        pre_screen_evidence={"summary": "Physical owner pre-screen evidence"},
    )
    db.add(application)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=int(user.organization_id),
        role_id=int(related.id),
        candidate_id=int(candidate.id),
        source_application_id=int(application.id),
        ats_application_id=int(application.id),
        status="done",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        application_outcome_source="recruiter",
        membership_source="initial_snapshot",
        spec_fingerprint="related-share-spec",
        cv_fingerprint="related-share-cv",
        role_fit_score=91,
        summary="Logical role evidence summary",
        details={
            "summary": "Logical role evidence summary",
            "requirements_match_score_100": 88,
            "requirements": [
                {
                    "requirement": "Production reliability",
                    "evidence_quote": "Led production incident response.",
                }
            ],
            "integrity_signals": {"recruiter_only": True},
        },
    )
    db.add(evaluation)
    db.commit()
    return owner, related, application, evaluation


def test_create_list_revoke_share_link(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "7d"},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    link = create.json()
    assert link["mode"] == "client"
    assert link["view_role_id"] is None
    assert link["expiry_preset"] == "7d"
    assert link["active"] is True
    assert link["revoked"] is False
    assert link["expired"] is False
    assert link["token"].startswith("shr_")
    assert link["expires_at"]

    listing = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert len(body["links"]) == 1
    assert body["links"][0]["id"] == link["id"]
    assert body["links"][0]["view_role_id"] is None

    # Mint a second link in a different mode + expiry to confirm
    # multiple active links per application is the new contract.
    second = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "recruiter", "expiry": "24h"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    listing2 = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing2.status_code == 200
    assert len(listing2.json()["links"]) == 2

    # Revoke the first link → it stays in the list but flips to
    # revoked / inactive so the report footer can render audit history.
    revoke = client.delete(
        f"/api/v1/share-links/{link['id']}",
        headers=headers,
    )
    assert revoke.status_code == 200, revoke.text
    revoked_payload = revoke.json()
    assert revoked_payload["revoked"] is True
    assert revoked_payload["active"] is False

    listing3 = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing3.status_code == 200
    by_id = {row["id"]: row for row in listing3.json()["links"]}
    assert by_id[link["id"]]["revoked"] is True
    assert by_id[second.json()["id"]]["revoked"] is False


def test_create_share_link_rejects_invalid_mode_or_expiry(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    bad_mode = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "internal", "expiry": "7d"},
        headers=headers,
    )
    assert bad_mode.status_code == 400

    bad_expiry = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "1y"},
        headers=headers,
    )
    assert bad_expiry.status_code == 400


def test_public_share_view_short_circuits_single_view_after_first_get(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "single-view", "expiry": "single-view"},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    token = create.json()["token"]

    first = client.get(f"/share/{token}")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["mode"] == "single-view"
    assert payload["application_id"] == application["id"]

    # Second GET against a single-view link returns 410 Gone.
    second = client.get(f"/share/{token}")
    assert second.status_code == 410


def test_public_share_view_rejects_revoked_and_expired_links(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "30d"},
        headers=headers,
    )
    assert create.status_code == 200
    link_id = create.json()["id"]
    token = create.json()["token"]

    revoke = client.delete(
        f"/api/v1/share-links/{link_id}",
        headers=headers,
    )
    assert revoke.status_code == 200
    revoked_view = client.get(f"/share/{token}")
    assert revoked_view.status_code == 410

    # Manually expire a fresh link in DB and confirm 410.
    fresh = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "24h"},
        headers=headers,
    )
    assert fresh.status_code == 200
    fresh_token = fresh.json()["token"]
    fresh_id = fresh.json()["id"]
    db = TestingSessionLocal()
    try:
        link = db.query(ShareLink).filter(ShareLink.id == fresh_id).first()
        assert link is not None
        link.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    expired_view = client.get(f"/share/{fresh_token}")
    assert expired_view.status_code == 410


def test_public_share_view_returns_full_application_payload(client):
    """The share-recipient endpoint returns the full application detail
    in one round-trip, scrubbed to client view when the link mode is
    ``client``. Without this, the SPA has no unauthenticated way to
    fetch the application — the share link would just render an empty page.
    """
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "7d"},
        headers=headers,
    )
    assert create.status_code == 200
    token = create.json()["token"]

    view = client.get(f"/share/{token}")
    assert view.status_code == 200, view.text
    payload = view.json()
    assert payload["application_id"] == application["id"]
    assert payload["mode"] == "client"
    assert payload["view"] == "client"
    assert "application" in payload
    inner = payload["application"]
    assert inner["id"] == application["id"]
    assert inner["candidate_email"] == "share-link@example.com"


def test_recruiter_share_includes_notes_and_timeline_client_does_not(client):
    """Recruiter shares are the full report: they carry the audit timeline
    + recruiter notes the authenticated detail view fetches via auth-only
    endpoints. Client shares must omit both (external, scrubbed)."""
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    recruiter_link = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "recruiter", "expiry": "7d"},
        headers=headers,
    )
    assert recruiter_link.status_code == 200, recruiter_link.text
    recruiter_view = client.get(f"/share/{recruiter_link.json()['token']}")
    assert recruiter_view.status_code == 200, recruiter_view.text
    recruiter_payload = recruiter_view.json()
    assert recruiter_payload["view"] == "recruiter"
    inner = recruiter_payload["application"]
    # Both surfaces are embedded (creating the application logs an event,
    # so the audit timeline is non-empty even without an assessment).
    assert isinstance(inner.get("application_events"), list)
    assert len(inner["application_events"]) >= 1
    assert isinstance(inner.get("recruiter_notes_timeline"), list)

    client_link = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "7d"},
        headers=headers,
    )
    assert client_link.status_code == 200, client_link.text
    client_view = client.get(f"/share/{client_link.json()['token']}")
    assert client_view.status_code == 200, client_view.text
    client_inner = client_view.json()["application"]
    assert client_inner.get("application_events") is None
    assert client_inner.get("recruiter_notes_timeline") is None


def test_public_share_view_bumps_view_count(client):
    headers, _ = auth_headers(client)
    _, application = _make_role_and_application(client, headers)

    create = client.post(
        f"/api/v1/applications/{application['id']}/share-links",
        json={"mode": "client", "expiry": "30d"},
        headers=headers,
    )
    assert create.status_code == 200
    token = create.json()["token"]

    for _ in range(3):
        view = client.get(f"/share/{token}")
        assert view.status_code == 200

    listing = client.get(
        f"/api/v1/applications/{application['id']}/share-links",
        headers=headers,
    )
    assert listing.status_code == 200
    row = listing.json()["links"][0]
    assert row["view_count"] == 3
    assert row["last_viewed_at"] is not None


def test_share_links_are_org_scoped(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")

    _, application_a = _make_role_and_application(client, headers_a)

    # Org A mints a link.
    create = client.post(
        f"/api/v1/applications/{application_a['id']}/share-links",
        json={"mode": "client", "expiry": "7d"},
        headers=headers_a,
    )
    assert create.status_code == 200
    link_id = create.json()["id"]

    # Org B cannot see, list, or revoke.
    list_b = client.get(
        f"/api/v1/applications/{application_a['id']}/share-links",
        headers=headers_b,
    )
    assert list_b.status_code == 404

    revoke_b = client.delete(
        f"/api/v1/share-links/{link_id}",
        headers=headers_b,
    )
    assert revoke_b.status_code == 404


def test_related_role_share_persists_and_serializes_logical_role_context(client, db):
    headers, email = auth_headers(client)
    _owner, related, application, _evaluation = _make_related_share_membership(
        db,
        user_email=email,
    )

    recruiter_link = client.post(
        f"/api/v1/applications/{application.id}/share-links",
        json={
            "mode": "recruiter",
            "expiry": "7d",
            "view_role_id": int(related.id),
        },
        headers=headers,
    )
    assert recruiter_link.status_code == 200, recruiter_link.text
    assert recruiter_link.json()["view_role_id"] == int(related.id)
    persisted = db.get(ShareLink, int(recruiter_link.json()["id"]))
    assert persisted.view_role_id == int(related.id)
    listing = client.get(
        f"/api/v1/applications/{application.id}/share-links",
        headers=headers,
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["links"][0]["view_role_id"] == int(related.id)

    recruiter_view = client.get(f"/share/{recruiter_link.json()['token']}")
    assert recruiter_view.status_code == 200, recruiter_view.text
    recruiter_payload = recruiter_view.json()
    assert recruiter_payload["view_role_id"] == int(related.id)
    inner = recruiter_payload["application"]
    assert inner["role_id"] == int(related.id)
    assert inner["role_name"] == "Logical Reliability Role"
    assert inner["pipeline_stage"] == "review"
    assert inner["application_outcome"] == "open"
    assert inner["taali_score"] == 91
    assert inner["rank_score"] == 91
    assert inner["pre_screen_score"] == 91
    assert inner["requirements_fit_score"] == 88
    assert inner["cv_match_score"] == 91
    assert inner["cv_match_details"]["summary"] == "Logical role evidence summary"
    assert inner["cv_match_details"]["requirements"][0]["evidence_quote"] == (
        "Led production incident response."
    )
    assert "source_role_score" not in inner
    assert "pre_screen_evidence" not in inner

    client_link = client.post(
        f"/api/v1/applications/{application.id}/share-links",
        json={
            "mode": "client",
            "expiry": "7d",
            "view_role_id": int(related.id),
        },
        headers=headers,
    )
    assert client_link.status_code == 200, client_link.text
    client_view = client.get(f"/share/{client_link.json()['token']}")
    assert client_view.status_code == 200, client_view.text
    client_inner = client_view.json()["application"]
    assert client_inner["role_id"] == int(related.id)
    assert client_inner["taali_score"] == 91
    assert client_inner["client_share_summary"]["role"] == (
        "Logical Reliability Role"
    )
    assert client_inner["client_share_summary"]["score_100"] == 91
    assert "integrity_signals" not in client_inner["cv_match_details"]
    assert client_inner["cv_match_details"]["requirements"][0]["requirement"] == (
        "Production reliability"
    )


def test_related_role_share_requires_a_live_exact_membership(client, db):
    headers, email = auth_headers(client)
    _owner, related, application, evaluation = _make_related_share_membership(
        db,
        user_email=email,
    )
    unrelated = Role(
        organization_id=int(related.organization_id),
        name="Unrelated Logical Role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        related_source_role_id=int(related.related_source_role_id),
        ats_owner_role_id=int(related.ats_owner_role_id),
        job_spec_text="A separate logical role.",
    )
    db.add(unrelated)
    db.commit()

    wrong_role = client.post(
        f"/api/v1/applications/{application.id}/share-links",
        json={
            "mode": "client",
            "expiry": "7d",
            "view_role_id": int(unrelated.id),
        },
        headers=headers,
    )
    assert wrong_role.status_code == 404

    valid = client.post(
        f"/api/v1/applications/{application.id}/share-links",
        json={
            "mode": "client",
            "expiry": "7d",
            "view_role_id": int(related.id),
        },
        headers=headers,
    )
    assert valid.status_code == 200, valid.text
    evaluation.deleted_at = datetime.now(timezone.utc)
    db.commit()

    unavailable = client.get(f"/share/{valid.json()['token']}")
    assert unavailable.status_code == 404
    db.expire_all()
    persisted = db.get(ShareLink, int(valid.json()["id"]))
    assert persisted.view_count == 0
