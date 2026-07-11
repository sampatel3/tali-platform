"""GDPR data-subject requests — access export, full-PII erasure (incl. the raw
ATS payloads the older build missed), reject path, owner gate."""
from app.domains.compliance.data_subject_service import _ERASE_FIELDS
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.user import User
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).first().organization_id


def _candidate_with_app(db, org_id: int, email="subject@dsr.test") -> Candidate:
    cand = Candidate(
        organization_id=org_id,
        email=email,
        full_name="Subject Person",
        phone="+971500000001",
        summary="ten years of stuff",
        # The raw ATS payloads — full third-party PII the old erasure missed.
        workable_data={"first_name": "Subject", "email": email, "resume_url": "x"},
        bullhorn_data={"firstName": "Subject", "email": email},
        company_name="Acme Corp",
        tags=["vip", "referral"],
        skills=["python", "sql"],
        workable_comments=[{"body": "great chat"}],
        workable_activities=[{"action": "emailed"}],
    )
    db.add(cand)
    db.flush()
    role = Role(organization_id=org_id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    db.add(
        CandidateApplication(
            organization_id=org_id,
            candidate_id=cand.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="applied",
            application_outcome="open",
            source="careers",
        )
    )
    db.commit()
    return cand


def test_access_request_returns_export(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cand = _candidate_with_app(db, org_id)

    rid = client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "access", "subject_email": cand.email},
        headers=headers,
    ).json()["id"]

    r = client.post(f"/api/v1/compliance/data-requests/{rid}/fulfill", headers=headers)
    assert r.status_code == 200, r.text
    export = r.json()["export"]
    assert export["candidate"]["email"] == cand.email
    assert len(export["applications"]) == 1

    # Completed — can't be re-fulfilled.
    assert client.post(
        f"/api/v1/compliance/data-requests/{rid}/fulfill", headers=headers
    ).status_code == 409


def test_email_subject_resolves_case_insensitively(client, db):
    # A candidate stored with a mixed-case email (imported/legacy rows keep the
    # original casing) must still resolve when the data subject submits any casing.
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cand = _candidate_with_app(db, org_id, email="Mixed.Case@Example.com")

    rid = client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "access", "subject_email": "mixed.case@example.COM"},
        headers=headers,
    ).json()["id"]

    r = client.post(f"/api/v1/compliance/data-requests/{rid}/fulfill", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["export"]["candidate"]["email"] == cand.email


def test_erasure_scrubs_every_pii_field_incl_raw_ats_payloads(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    cand = _candidate_with_app(db, org_id, email="erase@dsr.test")
    cid = cand.id

    rid = client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "erasure", "candidate_id": cid},
        headers=headers,
    ).json()["id"]
    r = client.post(f"/api/v1/compliance/data-requests/{rid}/fulfill", headers=headers)
    assert r.status_code == 200 and r.json()["erased"] is True

    db.expire_all()
    erased = db.query(Candidate).filter_by(id=cid).first()
    # EVERY enumerated erasure field is None — asserted individually so a
    # regression on any one column (e.g. the raw workable_data/bullhorn_data
    # payloads the old build left behind) fails loudly.
    for field in _ERASE_FIELDS:
        assert getattr(erased, field) is None, f"{field} was not scrubbed"
    # The reviewed-fix columns, called out explicitly.
    assert erased.workable_data is None and erased.bullhorn_data is None
    assert erased.company_name is None and erased.tags is None and erased.skills is None
    # Soft-deleted.
    assert erased.deleted_at is not None
    # The compliance log survives the erased candidate (durable evidence).
    assert client.get(
        "/api/v1/compliance/data-requests", headers=headers
    ).json()[0]["status"] == "completed"


def test_create_validation_and_reject(client, db):
    headers, _ = auth_headers(client)
    assert client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "nonsense", "subject_email": "x@y.test"}, headers=headers,
    ).status_code == 422
    assert client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "access"}, headers=headers,
    ).status_code == 422

    rid = client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "access", "subject_email": "x@y.test"}, headers=headers,
    ).json()["id"]
    r = client.post(
        f"/api/v1/compliance/data-requests/{rid}/reject",
        json={"reason": "identity not verified"}, headers=headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_data_requests_are_owner_only(client, db):
    headers, email = auth_headers(client)
    caller = db.query(User).filter(User.email == email).first()
    caller.role = "member"  # not owner
    db.commit()
    assert client.get(
        "/api/v1/compliance/data-requests", headers=headers
    ).status_code == 403
    assert client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "access", "subject_email": "x@y.test"}, headers=headers,
    ).status_code == 403
