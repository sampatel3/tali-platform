"""Outreach campaign routes — audience rails, two-phase generate, transitions.

Covers: create + default brief + job_page_token resolution; audience resolution
rails (suppressed / open_application / duplicate / missing_email / cap 413);
generate two-phase (estimate without confirm, task enqueued with confirm);
message edit/approve/reject transitions + only-approved counts; org isolation.
"""
from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.outreach_campaign import (
    MESSAGE_STATUS_APPROVED,
    MESSAGE_STATUS_DRAFT,
    MESSAGE_STATUS_PENDING,
    OutreachCampaign,
    OutreachMessage,
)
from app.models.user import User
from app.services.email_suppression_service import suppress
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).first().organization_id


def _make_role(db, org_id, name="Backend"):
    role = Role(organization_id=org_id, name=name, source="manual")
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _make_apply_role(db, org_id, name="Backend"):
    role = _make_role(db, org_id, name=name)
    role.workable_job_id = f"job-{role.id}"
    role.workable_job_data = {
        "application_url": f"https://apply.workable.com/acme/j/{role.id}/"
    }
    db.commit()
    db.refresh(role)
    return role


def _make_candidate_with_app(
    db, org_id, email, outcome="rejected", name="C", role=None, pipeline_stage="applied"
):
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
        pipeline_stage=pipeline_stage,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return cand, app


def _make_audience_app(db, org_id, campaign_id, email, name="C"):
    campaign = db.get(OutreachCampaign, int(campaign_id))
    role = db.get(Role, int(campaign.role_id))
    _candidate, app = _make_candidate_with_app(
        db,
        org_id,
        email,
        outcome="rejected",
        name=name,
        role=role,
    )
    return app


def _create_campaign(client, headers, name="Wave 1", role_id=None):
    if role_id is None:
        role_response = client.post(
            "/api/v1/roles",
            json={"name": f"{name.strip() or 'Outreach'} role"},
            headers=headers,
        )
        assert role_response.status_code == 201, role_response.text
        role_id = role_response.json()["id"]
    payload = {"name": name}
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


def test_create_requires_role(client):
    headers, _ = auth_headers(client)
    resp = client.post(
        "/api/v1/outreach/campaigns",
        json={"name": "Role-less wave"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_workable_campaign_captures_external_application_destination(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_role(db, org_id)
    role.workable_job_id = "engineering"
    role.workable_job_data = {
        "application_url": "https://apply.workable.com/acme/j/engineering/"
    }
    db.commit()

    resp = _create_campaign(client, headers, role_id=role.id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["destination_provider"] == "workable"
    assert resp.json()["destination_url"] == role.workable_job_data["application_url"]


# ---------------------------------------------------------------------------
# Audience rails
# ---------------------------------------------------------------------------


def test_audience_excludes_suppressed_open_dup_missing(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_role(db, org_id, name="Audience role")
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]

    good = _make_audience_app(db, org_id, cid, "good@example.com", name="Good")
    sup = _make_audience_app(db, org_id, cid, "blocked@example.com", name="Blocked")
    suppress(db, email="blocked@example.com", reason="unsubscribed", organization_id=org_id)
    # A candidate with an OPEN application → in-process, excluded.
    open_cand, open_app = _make_candidate_with_app(
        db, org_id, "open@example.com", outcome="open", role=role
    )
    # A candidate with a REJECTED application → eligible pool target.
    rej_cand, rej_app = _make_candidate_with_app(
        db, org_id, "pool@example.com", outcome="rejected", role=role
    )

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [good.id, sup.id, open_app.id, rej_app.id]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 2  # eligible sourced/pool applications
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
    app1 = _make_audience_app(db, org_id, cid, "dupe@example.com")

    r1 = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app1.id]},
        headers=headers,
    ).json()
    assert r1["added"] == 1

    # Adding the same email again → skipped as duplicate.
    app2 = _make_audience_app(db, org_id, cid, "dupe2@example.com")
    # Re-add the first application to exercise the existing campaign-email rail.
    r2 = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app1.id, app2.id]},
        headers=headers,
    ).json()
    assert r2["added"] == 1
    assert any(s.get("reason") == "duplicate" for s in r2["skipped"])


def test_audience_rejects_retired_prospect_ids_field(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    app = _make_audience_app(db, org_id, cid, "strict-schema@example.com")

    response = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id], "prospect_ids": [123]},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert db.query(OutreachMessage).filter_by(campaign_id=cid).count() == 0


def test_audience_missing_email(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    # A candidate with an application but no email.
    role = _make_role(db, org_id)
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]
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
    application_ids = []
    for i in range(201):
        app = _make_audience_app(db, org_id, cid, f"cap{i}@example.com")
        application_ids.append(app.id)
    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": application_ids},
        headers=headers,
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Generate (two-phase)
# ---------------------------------------------------------------------------


def test_generate_estimate_without_confirm(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_apply_role(db, org_id)
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]
    app = _make_audience_app(db, org_id, cid, "gen@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
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


def test_generate_requires_real_application_destination(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_role(db, org_id, name="Unpublished role")
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]
    app = _make_audience_app(db, org_id, cid, "no-destination@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
        headers=headers,
    )

    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/generate",
            json={"confirm": True},
            headers=headers,
        )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "application_destination_required"
    delay.assert_not_called()
    campaign = db.get(OutreachCampaign, int(cid))
    db.refresh(campaign)
    assert campaign.status == "draft"


def test_generate_reresolves_destination_added_after_creation(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_role(db, org_id, name="Destination added later")
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]
    app = _make_audience_app(db, org_id, cid, "destination-ready@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
        headers=headers,
    )
    role.workable_job_id = "late-job"
    role.workable_job_data = {
        "application_url": "https://apply.workable.com/acme/j/late-job/"
    }
    db.commit()

    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/generate",
            json={"confirm": True},
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    delay.assert_called_once_with(cid)
    campaign = db.get(OutreachCampaign, int(cid))
    db.refresh(campaign)
    assert campaign.destination_provider == "workable"
    assert campaign.destination_url == role.workable_job_data["application_url"]
    assert campaign.job_page_token is None


def test_generate_confirm_enqueues(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_apply_role(db, org_id)
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]
    app = _make_audience_app(db, org_id, cid, "gen2@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
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


def test_generate_broker_failure_restores_draft(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_apply_role(db, org_id)
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]
    app = _make_audience_app(db, org_id, cid, "retry-generate@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
        headers=headers,
    )

    with patch(
        "app.tasks.outreach_tasks.generate_campaign_drafts.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/generate",
            json={"confirm": True},
            headers=headers,
        )

    assert resp.status_code == 503, resp.text
    campaign = db.get(OutreachCampaign, int(cid))
    db.refresh(campaign)
    assert campaign.status == "draft"


def test_ready_campaign_rejects_audience_and_generation(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    _seed_draft(db, org_id, cid)
    app = _make_audience_app(db, org_id, cid, "too-late@example.com")

    audience = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
        headers=headers,
    )
    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as delay:
        generate = client.post(
            f"/api/v1/outreach/campaigns/{cid}/generate",
            json={"confirm": True},
            headers=headers,
        )

    assert audience.status_code == 409, audience.text
    assert generate.status_code == 409, generate.text
    delay.assert_not_called()
    assert (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.campaign_id == cid,
            OutreachMessage.email == "too-late@example.com",
        )
        .count()
        == 0
    )


def test_generate_while_generating_conflicts_without_duplicate_enqueue(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_apply_role(db, org_id)
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]
    app = _make_audience_app(db, org_id, cid, "gen-once@example.com")
    client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [app.id]},
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
    campaign = db.get(OutreachCampaign, int(cid))
    campaign.status = "ready"
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


def test_generating_campaign_blocks_message_and_send_mutations(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    message = _seed_draft(db, org_id, cid)
    app = _make_audience_app(db, org_id, cid, "blocked-mutation@example.com")
    campaign = db.get(OutreachCampaign, int(cid))
    campaign.status = "generating"
    db.commit()

    responses = [
        client.patch(
            f"/api/v1/outreach/campaigns/{cid}",
            json={"brief": "changed"},
            headers=headers,
        ),
        client.post(
            f"/api/v1/outreach/campaigns/{cid}/audience",
            json={"application_ids": [app.id]},
            headers=headers,
        ),
        client.post(
            f"/api/v1/outreach/campaigns/{cid}/messages/approve",
            json={"message_ids": [message.id]},
            headers=headers,
        ),
        client.post(
            f"/api/v1/outreach/campaigns/{cid}/messages/{message.id}",
            json={"subject": "changed"},
            headers=headers,
        ),
        client.post(
            f"/api/v1/outreach/campaigns/{cid}/messages/{message.id}/reject",
            headers=headers,
        ),
        client.post(
            f"/api/v1/outreach/campaigns/{cid}/send",
            json={"confirm": True},
            headers=headers,
        ),
    ]

    assert all(response.status_code == 409 for response in responses)
    db.refresh(campaign)
    db.refresh(message)
    assert campaign.status == "generating"
    assert campaign.brief != "changed"
    assert message.status == MESSAGE_STATUS_DRAFT
    assert message.subject == "Hi"


def test_draft_campaign_cannot_send(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    campaign = db.get(OutreachCampaign, int(cid))
    message = OutreachMessage(
        campaign_id=cid,
        organization_id=org_id,
        email="approved-too-early@example.com",
        status=MESSAGE_STATUS_APPROVED,
    )
    db.add(message)
    db.commit()

    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/send",
            json={"confirm": True},
            headers=headers,
        )

    assert resp.status_code == 409, resp.text
    delay.assert_not_called()
    db.refresh(campaign)
    db.refresh(message)
    assert campaign.status == "draft"
    assert message.status == MESSAGE_STATUS_APPROVED


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


def test_editing_approved_message_resets_approval_and_bumps_revision(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    message = _seed_draft(db, org_id, cid)
    message.status = MESSAGE_STATUS_APPROVED
    db.commit()

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/{message.id}",
        json={"subject": "Edited after approval"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == MESSAGE_STATUS_DRAFT
    db.refresh(message)
    campaign = db.get(OutreachCampaign, int(cid))
    db.refresh(campaign)
    assert message.status == MESSAGE_STATUS_DRAFT
    assert message.subject == "Edited after approval"
    assert campaign.review_revision == 1


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


def test_agent_campaign_rejects_legacy_approve_and_send(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    message = _seed_draft(db, org_id, cid)
    campaign = db.get(OutreachCampaign, int(cid))
    campaign.origin = "agent"
    db.commit()

    approve = client.post(
        f"/api/v1/outreach/campaigns/{cid}/messages/approve",
        json={"message_ids": [message.id]},
        headers=headers,
    )
    assert approve.status_code == 409, approve.text
    db.refresh(message)
    assert message.status == MESSAGE_STATUS_DRAFT

    # Even a historically/pre-existing approved row cannot use the legacy send
    # path to bypass the campaign-level reviewed snapshot.
    message.status = MESSAGE_STATUS_APPROVED
    db.commit()
    estimate = client.post(
        f"/api/v1/outreach/campaigns/{cid}/send",
        json={},
        headers=headers,
    )
    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        confirm = client.post(
            f"/api/v1/outreach/campaigns/{cid}/send",
            json={"confirm": True},
            headers=headers,
        )
    assert estimate.status_code == 409, estimate.text
    assert confirm.status_code == 409, confirm.text
    delay.assert_not_called()
    db.refresh(message)
    assert message.status == MESSAGE_STATUS_APPROVED


def test_campaign_lock_query_compiles_for_postgres(client, db):
    from app.domains.outreach import campaign_concurrency_service

    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]

    query = campaign_concurrency_service._owned_campaign_query(
        db,
        cid,
        org_id,
        for_update=True,
    )
    sql = str(query.statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in sql


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


# ---------------------------------------------------------------------------
# Approve & send all (one campaign-level HITL)
# ---------------------------------------------------------------------------


def test_approve_and_send_estimate_excludes_rejected_and_suppressed(client, db):
    """The confirm=false estimate reports what will actually go out: drafts +
    approved minus suppressed, with rejected/suppressed counts excluded."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    _seed_draft(db, org_id, cid, "d1@example.com")
    _seed_draft(db, org_id, cid, "d2@example.com")
    # A pre-approved draft (an earlier per-message approve) is still sendable.
    a = _seed_draft(db, org_id, cid, "a1@example.com")
    a.status = MESSAGE_STATUS_APPROVED
    # A rejected message (back to pending) is excluded.
    r = _seed_draft(db, org_id, cid, "rej@example.com")
    r.status = MESSAGE_STATUS_PENDING
    # A draft whose email is suppressed is counted but excluded from will_send.
    _seed_draft(db, org_id, cid, "sup@example.com")
    suppress(db, email="sup@example.com", reason="unsubscribed", organization_id=org_id)
    db.commit()

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send", json={}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sendable_count"] == 4  # d1, d2, a1, sup (draft/approved)
    assert body["suppressed_excluded"] == 1
    assert body["will_send"] == 3
    assert body["rejected_excluded"] == 1
    assert "status" not in body  # not enqueued without confirm


def test_approve_and_send_confirm_sends_all_pending(client, db):
    """Confirm approves every draft (and pre-approved) and enqueues one send;
    rejected/failed rows stay put."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    d1 = _seed_draft(db, org_id, cid, "d1@example.com")
    d2 = _seed_draft(db, org_id, cid, "d2@example.com")
    a1 = _seed_draft(db, org_id, cid, "a1@example.com")
    a1.status = MESSAGE_STATUS_APPROVED
    rej = _seed_draft(db, org_id, cid, "rej@example.com")
    rej.status = MESSAGE_STATUS_PENDING
    db.commit()

    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={"confirm": True},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "sending"
    delay.assert_called_once_with(cid)
    for m in (d1, d2, a1):
        db.refresh(m)
        assert m.status == "queued"
    db.refresh(rej)
    assert rej.status == MESSAGE_STATUS_PENDING  # rejected untouched
    campaign = db.query(OutreachCampaign).filter(OutreachCampaign.id == cid).one()
    approver = db.query(User).filter(User.email == email).one()
    assert campaign.approved_by_user_id == approver.id
    assert campaign.approved_at is not None


def test_approve_and_send_rejects_stale_reviewed_count(client, db):
    """HITL authorization applies only to the outbound count that was shown."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    draft = _seed_draft(db, org_id, cid, "d1@example.com")
    db.commit()

    estimate = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={},
        headers=headers,
    )
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["sendable_count"] == 1
    assert estimate.json()["will_send"] == 1
    suppress(
        db,
        email="d1@example.com",
        reason="unsubscribed",
        organization_id=org_id,
    )
    db.commit()

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={"confirm": True, "expected_will_send_count": 1},
        headers=headers,
    )

    assert resp.status_code == 409, resp.text
    assert "outbound audience changed" in resp.json()["detail"]
    db.refresh(draft)
    assert draft.status == MESSAGE_STATUS_DRAFT
    campaign = db.query(OutreachCampaign).filter(OutreachCampaign.id == cid).one()
    assert campaign.status == "ready"
    assert campaign.approved_by_user_id is None


def test_approve_and_send_rejects_changed_draft_with_same_count(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    draft = _seed_draft(db, org_id, cid, "d1@example.com")
    draft.subject = "Original subject"
    draft.body = "Original body"
    db.commit()

    estimate = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={},
        headers=headers,
    ).json()
    draft.subject = "Changed after review"
    db.commit()

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={
            "confirm": True,
            "expected_will_send_count": 1,
            "expected_review_token": estimate["review_token"],
        },
        headers=headers,
    )

    assert resp.status_code == 409, resp.text
    assert "drafts changed" in resp.json()["detail"]
    db.refresh(draft)
    assert draft.status == MESSAGE_STATUS_DRAFT


def test_agent_campaign_requires_review_snapshot(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    campaign = db.query(OutreachCampaign).filter(OutreachCampaign.id == cid).one()
    campaign.origin = "agent"
    _seed_draft(db, org_id, cid, "d1@example.com")
    db.commit()

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={"confirm": True, "expected_will_send_count": 1},
        headers=headers,
    )

    assert resp.status_code == 428, resp.text
    assert "Review the agent-prepared" in resp.json()["detail"]


def test_agent_campaign_approve_and_send_accepts_exact_review_snapshot(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    campaign = db.get(OutreachCampaign, int(cid))
    campaign.origin = "agent"
    message = _seed_draft(db, org_id, cid, "agent-reviewed@example.com")
    db.commit()
    estimate = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={},
        headers=headers,
    ).json()

    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={
                "confirm": True,
                "expected_will_send_count": estimate["will_send"],
                "expected_review_token": estimate["review_token"],
            },
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    delay.assert_called_once_with(cid)
    db.refresh(message)
    assert message.status == "queued"


def test_review_revision_rejects_edit_race_after_digest_validation(client, db):
    """SQLite ignores FOR UPDATE, so the persisted revision must still make an
    edit that starts after digest validation defeat the stale send claim."""
    from app.domains.outreach import campaign_service

    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    campaign = db.get(OutreachCampaign, int(cid))
    campaign.origin = "agent"
    message = _seed_draft(db, org_id, cid, "race@example.com")
    db.commit()
    reviewed = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={},
        headers=headers,
    ).json()
    original_estimate = campaign_service.approve_and_send_estimate

    def _estimate_then_interleave_edit(session, campaign_id, organization_id):
        estimate = original_estimate(session, campaign_id, organization_id)
        # This is the write claim an edit route performs before touching the
        # message. Injecting both writes in the request transaction gives
        # SQLite a deterministic post-digest interleaving; the stale status CAS
        # must fail and roll the simulated edit back before anything is queued.
        session.query(OutreachCampaign).filter(
            OutreachCampaign.id == campaign_id,
            OutreachCampaign.status == "ready",
        ).update(
            {
                OutreachCampaign.review_revision: (
                    OutreachCampaign.review_revision + 1
                ),
            },
            synchronize_session=False,
        )
        session.query(OutreachMessage).filter(
            OutreachMessage.id == message.id,
        ).update(
            {OutreachMessage.body: "Concurrent edit"},
            synchronize_session=False,
        )
        return estimate

    with patch.object(
        campaign_service,
        "approve_and_send_estimate",
        side_effect=_estimate_then_interleave_edit,
    ), patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={
                "confirm": True,
                "expected_will_send_count": reviewed["will_send"],
                "expected_review_token": reviewed["review_token"],
            },
            headers=headers,
        )

    assert resp.status_code == 409, resp.text
    assert "changed while this request" in resp.json()["detail"]
    delay.assert_not_called()
    db.refresh(campaign)
    db.refresh(message)
    assert campaign.status == "ready"
    assert campaign.review_revision == reviewed["review_revision"]
    assert message.status == MESSAGE_STATUS_DRAFT
    assert message.body == "Body {{cta_url}}"


def test_approve_and_send_broker_failure_restores_retryable_state(client, db):
    """A durable queue outage cannot strand a campaign in false sending state."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    draft = _seed_draft(db, org_id, cid, "d1@example.com")
    db.commit()

    with patch(
        "app.tasks.outreach_tasks.send_campaign_messages.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={"confirm": True, "expected_will_send_count": 1},
            headers=headers,
        )

    assert resp.status_code == 503, resp.text
    assert "safe to retry" in resp.json()["detail"]
    db.refresh(draft)
    assert draft.status == MESSAGE_STATUS_APPROVED
    campaign = db.query(OutreachCampaign).filter(OutreachCampaign.id == cid).one()
    approver = db.query(User).filter(User.email == email).one()
    assert campaign.status == "ready"
    assert campaign.approved_by_user_id == approver.id
    assert campaign.approved_at is not None


def test_approve_and_send_skips_already_sent(client, db):
    """A message already sent is never re-queued by the batch action."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    sent = _seed_draft(db, org_id, cid, "sent@example.com")
    sent.status = "sent"
    draft = _seed_draft(db, org_id, cid, "new@example.com")
    db.commit()

    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={"confirm": True},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    delay.assert_called_once_with(cid)
    db.refresh(sent)
    assert sent.status == "sent"  # not re-queued
    db.refresh(draft)
    assert draft.status == "queued"


def test_approve_and_send_idempotent_under_double_call(client, db):
    """A racing second confirm 409s and does not enqueue a second send; the
    atomic draft->queued flip means only one send task can ever run."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m = _seed_draft(db, org_id, cid, "d@example.com")
    db.commit()

    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        first = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={"confirm": True},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        db.refresh(m)
        assert m.status == "queued"
        duplicate = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={"confirm": True},
            headers=headers,
        )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Campaign is already sending"
    delay.assert_called_once_with(cid)


def test_approve_and_send_no_drafts_400(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    m = _seed_draft(db, org_id, cid, "rej@example.com")
    m.status = MESSAGE_STATUS_PENDING  # only a rejected/pending message
    db.commit()
    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={"confirm": True},
        headers=headers,
    )
    assert resp.status_code == 400


def test_approve_and_send_while_generating_409_no_queue(client, db):
    """A stale UI or direct call cannot batch-send while draft generation is
    still running: the campaign is 'generating', so the request 409s and no
    drafts are queued (the generator would otherwise overwrite the send)."""
    from app.models.outreach_campaign import OutreachCampaign

    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    d1 = _seed_draft(db, org_id, cid, "d1@example.com")
    d2 = _seed_draft(db, org_id, cid, "d2@example.com")
    campaign = db.query(OutreachCampaign).filter(OutreachCampaign.id == cid).first()
    campaign.status = "generating"
    db.commit()

    with patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as delay:
        resp = client.post(
            f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
            json={"confirm": True},
            headers=headers,
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "Campaign is still generating drafts"
    delay.assert_not_called()
    for m in (d1, d2):
        db.refresh(m)
        assert m.status == MESSAGE_STATUS_DRAFT  # nothing queued


def test_approve_and_send_archived_409(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cid = _create_campaign(client, headers).json()["id"]
    _seed_draft(db, org_id, cid)
    client.post(f"/api/v1/outreach/campaigns/{cid}/archive", headers=headers)
    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/approve-and-send",
        json={"confirm": True},
        headers=headers,
    )
    assert resp.status_code == 409


def test_audience_excludes_sourced_application_when_candidate_is_active_elsewhere(
    client, db
):
    """A sourced lead is excluded when its candidate is active on another role."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    active_role = _make_role(db, org_id, name="Active role")
    cand, _active_app = _make_candidate_with_app(
        db,
        org_id,
        "active-elsewhere@example.com",
        outcome="open",
        role=active_role,
    )
    sourcing_role = _make_role(db, org_id, name="Sourcing role")
    sourced_app = CandidateApplication(
        candidate_id=cand.id,
        organization_id=org_id,
        role_id=sourcing_role.id,
        application_outcome="open",
        pipeline_stage="sourced",
    )
    db.add(sourced_app)
    db.commit()
    db.refresh(sourced_app)
    cid = _create_campaign(client, headers, role_id=sourcing_role.id).json()["id"]

    r = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        headers=headers,
        json={"application_ids": [sourced_app.id]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 0
    assert body["skipped"][0]["reason"] == "open_application"


def test_audience_allows_sourced_but_excludes_applied(client, db):
    """Sourced leads are the point of outreach: a candidate whose only open
    application is at ``pipeline_stage='sourced'`` is a VALID target, while an
    open application at a real evaluation stage (``applied``) is still excluded."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _make_role(db, org_id, name="Sourcing role")
    cid = _create_campaign(client, headers, role_id=role.id).json()["id"]

    # Sourced lead: open outcome, pre-application stage → reachable.
    _sourced_cand, sourced_app = _make_candidate_with_app(
        db,
        org_id,
        "lead@example.com",
        outcome="open",
        pipeline_stage="sourced",
        role=role,
    )
    # Real applicant: open outcome, applied stage → in-process, excluded.
    _applied_cand, applied_app = _make_candidate_with_app(
        db,
        org_id,
        "applicant@example.com",
        outcome="open",
        pipeline_stage="applied",
        role=role,
    )

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [sourced_app.id, applied_app.id]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 1  # only the sourced lead
    assert any(
        s.get("reason") == "open_application" and s.get("email") == "applicant@example.com"
        for s in body["skipped"]
    )

    rows = db.query(OutreachMessage).filter(OutreachMessage.campaign_id == cid).all()
    assert {r.email for r in rows} == {"lead@example.com"}
    sourced_row = rows[0]
    assert sourced_row.source_application_id == sourced_app.id


def test_audience_excludes_candidate_with_both_sourced_and_applied_open(client, db):
    """If a candidate holds BOTH a sourced and a real open application, the real
    one wins: they are in-process and must not be an outbound target."""
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)

    role_a = _make_role(db, org_id, name="Role-A")
    role_b = _make_role(db, org_id, name="Role-B")
    cid = _create_campaign(client, headers, role_id=role_a.id).json()["id"]
    cand = Candidate(organization_id=org_id, email="both@example.com", full_name="Both")
    db.add(cand)
    db.flush()
    sourced_app = CandidateApplication(
        candidate_id=cand.id, organization_id=org_id, role_id=role_a.id,
        application_outcome="open", pipeline_stage="sourced",
    )
    applied_app = CandidateApplication(
        candidate_id=cand.id, organization_id=org_id, role_id=role_b.id,
        application_outcome="open", pipeline_stage="applied",
    )
    db.add_all([sourced_app, applied_app])
    db.commit()
    db.refresh(sourced_app)

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [sourced_app.id]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 0
    assert body["skipped"][0]["reason"] == "open_application"


def test_role_campaign_rejects_application_from_another_role(client, db):
    """Application ids cannot cross the campaign's role boundary.

    Otherwise a caller could charge Role A's budget while drafting outreach
    grounded in Role A for a sourced lead that actually belongs to Role B.
    """
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role_a = _make_role(db, org_id, name="Role-A")
    role_b = _make_role(db, org_id, name="Role-B")
    _cand, sourced_app = _make_candidate_with_app(
        db,
        org_id,
        "other-role@example.com",
        outcome="open",
        pipeline_stage="sourced",
        role=role_b,
    )
    cid = _create_campaign(client, headers, role_id=role_a.id).json()["id"]

    resp = client.post(
        f"/api/v1/outreach/campaigns/{cid}/audience",
        json={"application_ids": [sourced_app.id]},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["added"] == 0
    assert resp.json()["skipped"] == [
        {
            "id": sourced_app.id,
            "email": "other-role@example.com",
            "reason": "wrong_role",
        }
    ]
    assert db.query(OutreachMessage).filter(OutreachMessage.campaign_id == cid).count() == 0
