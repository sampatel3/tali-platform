"""P5: GDPR data-subject requests — access export, erasure, admin gate."""
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.user import ROLE_RECRUITER, User
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).first().organization_id


def _candidate_with_app(db, org_id: int, email="subject@dsr.test") -> Candidate:
    cand = Candidate(
        organization_id=org_id, email=email, full_name="Subject Person",
        phone="+971500000001", summary="ten years of stuff",
    )
    db.add(cand)
    db.flush()
    role = Role(organization_id=org_id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    db.add(CandidateApplication(
        organization_id=org_id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied", application_outcome="open", source="careers",
    ))
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

    # Request is now completed and can't be re-fulfilled.
    assert client.post(
        f"/api/v1/compliance/data-requests/{rid}/fulfill", headers=headers
    ).status_code == 409


def test_erasure_anonymizes_and_soft_deletes(client, db):
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
    assert erased.email is None and erased.full_name is None and erased.phone is None
    assert erased.deleted_at is not None


def test_create_validation_and_reject(client, db):
    headers, _ = auth_headers(client)
    # Bad type.
    assert client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "nonsense", "subject_email": "x@y.test"}, headers=headers,
    ).status_code == 422
    # Neither email nor candidate_id.
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


def test_data_requests_are_admin_only(client, db):
    headers, email = auth_headers(client)
    caller = db.query(User).filter(User.email == email).first()
    caller.role = ROLE_RECRUITER  # not admin
    db.commit()
    assert client.get("/api/v1/compliance/data-requests", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/compliance/data-requests",
        json={"request_type": "access", "subject_email": "x@y.test"}, headers=headers,
    ).status_code == 403
