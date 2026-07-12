"""Outreach campaign routes — audience rails, two-phase generate, transitions.

Covers: create + default brief + job_page_token resolution; audience resolution
rails (suppressed / open_application / duplicate / missing_email / cap 413);
generate two-phase (estimate without confirm, task enqueued with confirm);
message edit/approve/reject transitions + only-approved counts; org isolation.
"""
from __future__ import annotations

from unittest.mock import patch

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.outreach_campaign import (
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_PENDING,
    OutreachMessage,
)
from app.models.prospect import Prospect
from app.models.user import User
from app.services.email_suppression_service import suppress
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).first().organization_id


def _make_prospect(db, org_id, email, name="P", status="new", candidate_id=None):
    p = Prospect(
        organization_id=org_id,
        full_name=name,
        email=email,
        status=status,
        candidate_id=candidate_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_role(db, org_id, name="Backend"):
    role = Role(organization_id=org_id, name=name, source="manual")
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _make_candidate_with_app(db, org_id, email, outcome="rejected", name="C", role=None):
    if role is None:
        role = _make_role(db, org_id, name=f"Role-{email}")
    cand = Candidate(organization_id=org_id, email=email, full_name=name)
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        candidate_id=cand.id,
        organization_id=org_id,
        role_id=role.id,
        application_outcome=outcome,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return cand, app


def _create_campaign(client, headers, name="Wave 1", role_id=None):
    payload = {"name": name}
    if role_id is not None:
        payload["role_id"] = role_id
    return client.post("/api/v1/outreach/campaigns", json=payload, headers=headers)


# ---------------------------------------------------------------------------
# Create / detail
# ---------------------------------------------------------------------------


def test_create_campaign_defaults(client):
    headers, _ = auth_headers(client)
    resp = _create_campaign(client, headers, name="Backend Wave")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Backend Wave"
    assert body["status"] == "draft"
    assert body["brief"]  # deterministic default brief present
    assert body["job_page_token"] is None


def test_create_requires_name(client):
    headers, _ = auth_headers(client)
    resp = _create_campaign(client, headers, name="   ")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Audience rails
# ---------------------------------------------------------------------------


def test_audience_excludes_suppressed_open_dup_missing(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]

    good = _make_prospect(db, org_id, "good@example.com", name="Good")
    sup = _make_prospect(db, org_id, "blocked@example.com", name="Blocked")
    suppress(db, email="blocked@example.com", reason="unsubscribed", organization_id=org_id)
    # A candidate with an OPEN application → in-process, excluded.
    open_cand, open_app = _make_candidate_with_app(db, org_id, "open@example.com", outcome="open")
    # A candidate with a REJECTED application → eligible pool target.
    rej_cand, rej_app = _make_candidate_with_app(db, org_id, "pool@example.com", outcome="rejected")

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={
            "prospect_ids": [good.id, sup.id],
            "application_ids": [open_app.id, rej_app.id],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 2  # good prospect + pool candidate
    reasons = {s.get("reason") for s in body["skipped"]}
    assert "suppressed" in reasons
    assert "open_application" in reasons

    rows = db.query(OutreachMessage).filter(OutreachMessage.campaign_id == cid).all()
    emails = {r.email for r in rows}
    assert emails == {"good@example.com", "pool@example.com"}
    # source_application_id carried on the pool candidate row.
    pool_row = next(r for r in rows if r.email == "pool@example.com")
    assert pool_row.source_application_id == rej_app.id


def test_audience_duplicate_within_and_across_calls(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    p1 = _make_prospect(db, org_id, "dupe@example.com")

    r1 = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"prospect_ids": [p1.id]},
        headers=headers,
    ).json()
    assert r1["added"] == 1

    # Adding the same email again → skipped as duplicate.
    p2 = _make_prospect(db, org_id, "dupe2@example.com")
    # Force a second prospect with the SAME email is blocked by prospect unique
    # constraint, so re-add the same prospect id to exercise the campaign-dup path.
    r2 = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"prospect_ids": [p1.id, p2.id]},
        headers=headers,
    ).json()
    assert r2["added"] == 1
    assert any(s.get("reason") == "duplicate" for s in r2["skipped"])


def test_audience_missing_email(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    # A candidate with an application but no email.
    role = _make_role(db, org_id)
    cand = Candidate(organization_id=org_id, email=None, full_name="No Email")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        candidate_id=cand.id, organization_id=org_id, role_id=role.id,
        application_outcome="rejected",
    )
    db.add(app)
    db.commit()
    db.refresh(app)

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
        headers=headers,
    ).json()
    assert resp["added"] == 0
    assert any(s.get("reason") == "missing_email" for s in resp["skipped"])


def test_audience_cap_413(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    pids = []
    for i in range(201):
        p = _make_prospect(db, org_id, f"cap{i}@example.com")
        pids.append(p.id)
    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"prospect_ids": pids},
        headers=headers,
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Generate (two-phase)
# ---------------------------------------------------------------------------


def test_generate_estimate_without_confirm(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    p = _make_prospect(db, org_id, "gen@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"prospect_ids": [p.id]},
        headers=headers,
    )
    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/generate", json={}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["estimated_cost_usd"] >= 0
    assert "status" not in body  # not enqueued


def test_generate_confirm_enqueues(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    p = _make_prospect(db, org_id, "gen2@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"prospect_ids": [p.id]},
        headers=headers,
    )
    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/generate",
            json={"confirm": True},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "generating"
    delay.assert_called_once_with(cid)


def test_generate_while_generating_conflicts_without_duplicate_enqueue(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    p = _make_prospect(db, org_id, "gen-once@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"prospect_ids": [p.id]},
        headers=headers,
    )

    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as delay:
        first = client.post(
            f"/api/v1/outreach/campaigns/{cid}/generate",
            json={"confirm": True},
            headers=headers,
        )
        duplicate = client.post(
            f"/api/v1/outreach/campaigns/{cid}/generate",
            json={"confirm": True},
            headers=headers,
        )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"] == "Campaign is already generating"
    delay.assert_called_once_with(cid)


# ---------------------------------------------------------------------------
# Edit / approve / reject transitions
# ---------------------------------------------------------------------------


def _seed_draft(db, org_id, cid, email="draft@example.com"):
    m = OutreachMessage(
        campaign_id=cid,
        organization_id=org_id,
        email=email,
        recipient_name="D",
        subject="Hi",
        body="Body {{cta_url}}",
        status=MESSAGE_STATUS_DRAFT,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def test_edit_only_draft_or_approved(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m = _seed_draft(db, org_id, cid)
    # editable while draft
    r = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/{m.id}",
        json={"subject": "New subj"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["subject"] == "New subj"
    # pending message is not editable
    m.status = MESSAGE_STATUS_PENDING
    db.commit()
    r2 = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/{m.id}",
        json={"body": "x"},
        headers=headers,
    )
    assert r2.status_code == 409


def test_approve_ids_and_all_drafts(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m1 = _seed_draft(db, org_id, cid, "a@example.com")
    m2 = _seed_draft(db, org_id, cid, "b@example.com")

    r = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/approve",
        json={"message_ids": [m1.id]},
        headers=headers,
    )
    assert r.json()["approved"] == 1
    db.refresh(m1)
    assert m1.status == MESSAGE_STATUS_APPROVED

    r2 = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/approve",
        json={"all_drafts": True},
        headers=headers,
    )
    assert r2.json()["approved"] == 1  # only m2 remained a draft
    db.refresh(m2)
    assert m2.status == MESSAGE_STATUS_APPROVED


def test_reject_returns_to_pending(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m = _seed_draft(db, org_id, cid)
    r = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/{m.id}/reject", headers=headers
    )
    assert r.status_code == 200
    db.refresh(m)
    assert m.status == MESSAGE_STATUS_PENDING


def test_send_two_phase_confirm(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m = _seed_draft(db, org_id, cid)
    m.status = MESSAGE_STATUS_APPROVED
    db.commit()

    # estimate
    r = client.post(f"/api/v1/outreach/campaigns/{cid}/send", json={}, headers=headers)
    assert r.json()["approved_count"] == 1
    # confirm enqueues
    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        r2 = client.post(
            f"/api/v1/outreach/campaigns/{cid}/send",
            json={"confirm": True},
            headers=headers,
        )
    assert r2.json()["status"] == "sending"
    delay.assert_called_once_with(cid)


def test_send_no_approved_400(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    _seed_draft(db, org_id, cid)  # a draft, not approved
    r = client.post(
        f"/api/v1/outreach/campaigns/{cid}/send", json={"confirm": True}, headers=headers
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Org isolation
# ---------------------------------------------------------------------------


def test_org_isolation(client, db):
    h1, e1 = auth_headers(client, organization_name="Org1")
    h2, e2 = auth_headers(client, email="other@example.com", organization_name="Org2")
    cid = _create_campaign(client, h1).json()["id"]

    # Org2 cannot see or mutate Org1's campaign.
    assert client.get(f"/api/v1/outreach/campaigns/{cid}", headers=h2).status_code == 404
    assert (
        client.post(
            f"/api/v1/outreach/campaigns/{cid}/audience", json={}, headers=h2
        ).status_code
        == 404
    )
    # Org1 sees it in its list; Org2 does not.
    assert any(
        c["id"] == cid for c in client.get("/api/v1/outreach/campaigns", headers=h1).json()["campaigns"]
    )
    assert not any(
        c["id"] == cid for c in client.get("/api/v1/outreach/campaigns", headers=h2).json()["campaigns"]
    )


def test_send_while_sending_409_and_queued_flip(client, db):
    """Second confirmed send 409s, and the atomic approved->queued flip means a
    racing duplicate task would find zero rows to select."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m = _seed_draft(db, org_id, cid)
    m.status = MESSAGE_STATUS_APPROVED
    db.commit()

    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        r1 = client.post(
            f"/api/v1/outreach/campaigns/{cid}/send", json={"confirm": True}, headers=headers
        )
        assert r1.status_code == 200, r1.text
        db.refresh(m)
        assert m.status == "queued"

        r2 = client.post(
            f"/api/v1/outreach/campaigns/{cid}/send", json={"confirm": True}, headers=headers
        )
        assert r2.status_code == 409
        delay.assert_called_once_with(cid)


def test_reject_sent_message_409(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m = _seed_draft(db, org_id, cid)
    m.status = "sent"
    db.commit()

    r = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/{m.id}/reject", headers=headers
    )
    assert r.status_code == 409
    db.refresh(m)
    assert m.status == "sent"


def test_audience_excludes_linked_prospect_with_open_application(client, db):
    """A prospect linked to a candidate whose open application is under a
    DIFFERENT email must still be excluded."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cand, _app = _make_candidate_with_app(
        db, org_id, "work-alias@example.com", outcome="open"
    )
    p = _make_prospect(
        db, org_id, "personal@example.com", name="Open App", candidate_id=cand.id
    )
    cid = _create_campaign(client, headers).json()["id"]

    r = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        headers=headers,
        json={"prospect_ids": [p.id], "application_ids": []},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 0
    assert body["skipped"][0]["reason"] == "open_application"
