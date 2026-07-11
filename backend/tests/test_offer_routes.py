"""ATS slice B: offer lifecycle API + HRIS / e-sign export."""
from app.models import Candidate, CandidateApplication, Organization, Role, User
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).first().organization_id


def _seed_application(db, org_id: int) -> int:
    """An application in the caller's org, with a role + candidate rich enough
    to exercise the HRIS payload."""
    role = Role(
        organization_id=org_id, name="Staff Engineer", source="manual",
    )
    db.add(role)
    db.flush()
    cand = Candidate(
        organization_id=org_id, email="c@ofr.test", full_name="Casey R",
        phone="+971500000000",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="advanced", application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.commit()
    return app.id


def test_offer_lifecycle_create_get_list_transition(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_application(db, _org_id(db, email))

    r = client.post(
        f"/api/v1/applications/{app_id}/offers",
        json={"base_salary_amount": 200000, "currency": "AED", "pay_frequency": "year"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    offer = r.json()
    assert offer["version"] == 1 and offer["status"] == "draft"
    oid = offer["id"]

    r = client.get(f"/api/v1/offers/{oid}", headers=headers)
    assert r.status_code == 200 and r.json()["currency"] == "AED"

    r = client.get(f"/api/v1/applications/{app_id}/offers", headers=headers)
    assert [o["id"] for o in r.json()] == [oid]

    # draft -> sent (no approvals) -> accepted.
    assert client.post(f"/api/v1/offers/{oid}/transition", json={"status": "sent"}, headers=headers).status_code == 200
    r = client.post(f"/api/v1/offers/{oid}/transition", json={"status": "accepted"}, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "accepted"

    # Illegal transition (accepted is terminal) -> 409.
    r = client.post(f"/api/v1/offers/{oid}/transition", json={"status": "draft"}, headers=headers)
    assert r.status_code == 409


def test_offer_approvals_gate_the_approved_transition(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_application(db, _org_id(db, email))
    oid = client.post(
        f"/api/v1/applications/{app_id}/offers", json={}, headers=headers
    ).json()["id"]

    # Require one approval, then move to pending_approval.
    aid = client.post(
        f"/api/v1/offers/{oid}/approvals",
        json={"group_order": 0, "group_quorum": 1},
        headers=headers,
    ).json()["id"]
    assert client.post(
        f"/api/v1/offers/{oid}/transition", json={"status": "pending_approval"}, headers=headers
    ).status_code == 200

    # Approval unmet -> can't approve.
    assert client.post(
        f"/api/v1/offers/{oid}/transition", json={"status": "approved"}, headers=headers
    ).status_code == 409

    # Record the approval -> approve now succeeds.
    assert client.post(
        f"/api/v1/offers/{oid}/approvals/{aid}/record", json={"approved": True}, headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/v1/offers/{oid}/transition", json={"status": "approved"}, headers=headers
    ).status_code == 200


def test_offer_draft_to_sent_blocked_when_approval_pending(client, db):
    # Fix (a) over HTTP: a draft with a pending approval can't skip to sent.
    headers, email = auth_headers(client)
    app_id = _seed_application(db, _org_id(db, email))
    oid = client.post(
        f"/api/v1/applications/{app_id}/offers", json={}, headers=headers
    ).json()["id"]
    client.post(
        f"/api/v1/offers/{oid}/approvals", json={"group_order": 0}, headers=headers
    )
    r = client.post(f"/api/v1/offers/{oid}/transition", json={"status": "sent"}, headers=headers)
    assert r.status_code == 409


def test_hris_export_shape_and_ready_flag(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_application(db, _org_id(db, email))
    oid = client.post(
        f"/api/v1/applications/{app_id}/offers",
        json={"base_salary_amount": 180000, "currency": "AED", "pay_frequency": "year"},
        headers=headers,
    ).json()["id"]

    r = client.get(f"/api/v1/offers/{oid}/hris-export", headers=headers)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["offer"]["hris_ready"] is False  # still draft
    assert p["employee"] == {"full_name": "Casey R", "email": "c@ofr.test", "phone": "+971500000000"}
    assert p["position"]["title"] == "Staff Engineer"
    # Department/location fields are carried as None when the role has no such
    # columns yet — the payload shape is still complete.
    assert "department" in p["position"]
    assert p["position"]["location"] == {"city": None, "country": None}
    assert p["compensation"]["currency"] == "AED"
    assert p["compensation"]["base_salary_amount"] == 180000
    assert p["source"]["application_id"] == app_id

    # Once accepted, the payload flips to hris_ready with an accepted_at.
    client.post(f"/api/v1/offers/{oid}/transition", json={"status": "sent"}, headers=headers)
    client.post(f"/api/v1/offers/{oid}/transition", json={"status": "accepted"}, headers=headers)
    p = client.get(f"/api/v1/offers/{oid}/hris-export", headers=headers).json()
    assert p["offer"]["hris_ready"] is True
    assert p["dates"]["accepted_at"] is not None


def test_esign_request_shape_and_ready_flag(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_application(db, _org_id(db, email))
    oid = client.post(
        f"/api/v1/applications/{app_id}/offers",
        json={"base_salary_amount": 180000, "currency": "AED", "pay_frequency": "year"},
        headers=headers,
    ).json()["id"]

    p = client.get(f"/api/v1/offers/{oid}/esign-request", headers=headers).json()
    assert p["ready_to_send"] is False  # draft
    assert p["signers"][0] == {"role": "candidate", "name": "Casey R", "email": "c@ofr.test"}
    assert p["document"]["reference"] == f"offer-{oid}-v1"
    assert "Staff Engineer" in p["document"]["title"]
    assert p["prefill_fields"]["currency"] == "AED"

    # Approve (no approvals required) then it's ready to send for signature.
    client.post(f"/api/v1/offers/{oid}/transition", json={"status": "pending_approval"}, headers=headers)
    client.post(f"/api/v1/offers/{oid}/transition", json={"status": "approved"}, headers=headers)
    p = client.get(f"/api/v1/offers/{oid}/esign-request", headers=headers).json()
    assert p["ready_to_send"] is True


def test_offer_is_org_scoped(client, db):
    headers, email = auth_headers(client)
    _seed_application(db, _org_id(db, email))

    # An application in another org is not reachable.
    other = Organization(name="Other", slug="other-ofr")
    db.add(other)
    db.flush()
    ocand = Candidate(organization_id=other.id, email="o2@x.test", full_name="O2")
    orole = Role(organization_id=other.id, name="X2", source="manual")
    db.add_all([ocand, orole])
    db.flush()
    other_app = CandidateApplication(
        organization_id=other.id, candidate_id=ocand.id, role_id=orole.id,
        status="applied", pipeline_stage="applied", application_outcome="open", source="manual",
    )
    db.add(other_app)
    db.commit()
    r = client.post(f"/api/v1/applications/{other_app.id}/offers", json={}, headers=headers)
    assert r.status_code == 404


def test_add_approval_with_invalid_approver_is_404(client, db):
    headers, email = auth_headers(client)
    app_id = _seed_application(db, _org_id(db, email))
    oid = client.post(
        f"/api/v1/applications/{app_id}/offers", json={}, headers=headers
    ).json()["id"]

    # Unknown user id.
    r = client.post(
        f"/api/v1/offers/{oid}/approvals",
        json={"group_order": 0, "approver_user_id": 999999},
        headers=headers,
    )
    assert r.status_code == 404

    # A user from another organization.
    other = Organization(name="OtherAppr", slug="other-appr-rt")
    db.add(other)
    db.flush()
    outsider = User(
        email="outsider@appr-rt.test", hashed_password="x", is_active=True,
        is_superuser=False, is_verified=False, organization_id=other.id, role="member",
    )
    db.add(outsider)
    db.commit()
    r = client.post(
        f"/api/v1/offers/{oid}/approvals",
        json={"group_order": 0, "approver_user_id": outsider.id},
        headers=headers,
    )
    assert r.status_code == 404

    # A real same-org user is accepted.
    me = db.query(User).filter(User.email == email).first()
    r = client.post(
        f"/api/v1/offers/{oid}/approvals",
        json={"group_order": 0, "approver_user_id": me.id},
        headers=headers,
    )
    assert r.status_code == 201 and r.json()["approver_user_id"] == me.id
