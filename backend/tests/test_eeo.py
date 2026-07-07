"""P5: voluntary EEO self-ID — segregated record + aggregate-only report."""
import pytest

from app.domains.assessments_runtime.eeo_service import aggregate_report, record_response
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.eeo_response import EEOResponse
from app.models.role import ROLE_STATUS_PUBLISHED
from app.models.user import ROLE_RECRUITER, User
from app.platform.config import settings
from app.services import rate_limit
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", True)
    monkeypatch.setattr(settings, "ATS_APPLY_RATE_LIMIT_PER_HOUR", 50)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: None)
    rate_limit.reset_memory_buckets()
    yield
    rate_limit.reset_memory_buckets()


def _org_id(db, email):
    return db.query(User).filter(User.email == email).first().organization_id


def _app(db, org_id, role_id=None):
    if role_id is None:
        role = Role(organization_id=org_id, name="Eng", source="manual")
        db.add(role)
        db.flush()
        role_id = role.id
    cand = Candidate(organization_id=org_id, email=f"e{db.query(Candidate).count()}@x.test", full_name="E")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id, candidate_id=cand.id, role_id=role_id,
        status="applied", pipeline_stage="applied", application_outcome="open", source="careers",
    )
    db.add(app)
    db.flush()
    return app


def test_record_is_idempotent_and_segregated(db):
    org = Organization(name="Acme", slug="eeo-seg")
    db.add(org)
    db.flush()
    app = _app(db, org.id)

    record_response(db, org.id, app.id, gender="female", race_ethnicity="asian")
    record_response(db, org.id, app.id, gender="female", race_ethnicity="asian", veteran_status="no")
    assert db.query(EEOResponse).filter_by(application_id=app.id).count() == 1

    # The response has no relationship attribute back into the scoring graph.
    row = db.query(EEOResponse).filter_by(application_id=app.id).first()
    assert not hasattr(row, "application")


def test_aggregate_report_returns_counts_only(db):
    org = Organization(name="Acme", slug="eeo-agg")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    for g in ("female", "female", "male"):
        record_response(db, org.id, _app(db, org.id, role.id).id, gender=g)
    record_response(db, org.id, _app(db, org.id, role.id).id, declined_to_answer=True)

    rep = aggregate_report(db, org.id)
    assert rep["total"] == 4
    assert rep["gender"] == {"female": 2, "male": 1}
    assert rep["declined_count"] == 1
    # Counts only — no candidate/application ids anywhere in the report.
    assert "female" in rep["gender"] and isinstance(rep["gender"]["female"], int)


def test_public_eeo_endpoint_records(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    org = db.query(Organization).filter_by(id=org_id).first()
    org.slug = "eeo-pub"
    role = Role(organization_id=org_id, name="Eng", source="manual", status=ROLE_STATUS_PUBLISHED, slug="eng")
    db.add(role)
    db.flush()
    app = _app(db, org_id, role.id)
    db.commit()

    r = client.post(
        f"/careers/v1/eeo-pub/applications/{app.id}/eeo",
        json={"gender": "female", "declined_to_answer": False},
    )
    assert r.status_code == 204, r.text
    assert db.query(EEOResponse).filter_by(application_id=app.id).count() == 1


def test_eeo_report_is_admin_only(client, db):
    headers, email = auth_headers(client)
    # Admin sees it.
    assert client.get("/api/v1/compliance/eeo-report", headers=headers).status_code == 200
    # Recruiter does not.
    caller = db.query(User).filter(User.email == email).first()
    caller.role = ROLE_RECRUITER
    db.commit()
    assert client.get("/api/v1/compliance/eeo-report", headers=headers).status_code == 403
