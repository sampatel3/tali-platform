"""Voluntary EEO self-ID — token-authorised public write, segregated aggregate,
owner-only report with small-cell suppression."""
import pytest

from app.domains.compliance.eeo_service import (
    aggregate_report,
    record_response,
    suppress_small_cells,
)
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.eeo_response import EEOResponse
from app.models.user import User
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


def _app(db, org_id, role_id=None, *, eeo_token=None):
    if role_id is None:
        role = Role(organization_id=org_id, name="Eng", source="manual")
        db.add(role)
        db.flush()
        role_id = role.id
    cand = Candidate(
        organization_id=org_id,
        email=f"e{db.query(Candidate).count()}@x.test",
        full_name="E",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role_id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        source="careers",
        eeo_token=eeo_token,
    )
    db.add(app)
    db.flush()
    return app


# --- service ------------------------------------------------------------- #

def test_record_is_idempotent_and_segregated(db):
    org = Organization(name="Acme", slug="eeo-seg")
    db.add(org)
    db.flush()
    app = _app(db, org.id)

    record_response(db, org.id, app.id, gender="female", race_ethnicity="asian")
    record_response(
        db, org.id, app.id, gender="female", race_ethnicity="asian", veteran_status="no"
    )
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


def test_suppress_small_cells_masks_low_counts():
    raw = {
        "total": 12,
        "declined_count": 1,
        "gender": {"female": 7, "male": 3},
        "race_ethnicity": {},
        "veteran_status": {},
        "disability_status": {},
    }
    out = suppress_small_cells(raw, min_count=5)
    # A cell at/above threshold keeps its real count; a low cell reads "<5".
    assert out["gender"]["female"] == 7
    assert out["gender"]["male"] == "<5"
    # Org-wide totals are not protected cells — pass through.
    assert out["total"] == 12 and out["declined_count"] == 1


# --- public token-authorised write --------------------------------------- #

def test_public_eeo_records_via_token(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    app = _app(db, org_id, eeo_token="eeo_A")
    db.commit()

    r = client.post("/api/v1/public/eeo/eeo_A", json={"gender": "female"})
    assert r.status_code == 204, r.text
    # Overwrite-own-only: re-posting the same token updates the SAME row.
    r2 = client.post(
        "/api/v1/public/eeo/eeo_A", json={"gender": "female", "veteran_status": "no"}
    )
    assert r2.status_code == 204
    assert db.query(EEOResponse).filter_by(application_id=app.id).count() == 1


def test_token_for_app_a_cannot_write_app_b(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    app_a = _app(db, org_id, eeo_token="eeo_AA")
    app_b = _app(db, org_id, eeo_token="eeo_BB")
    db.commit()

    # Posting A's token records ONLY against A — there is no way to name B's id.
    assert client.post("/api/v1/public/eeo/eeo_AA", json={"gender": "male"}).status_code == 204
    assert db.query(EEOResponse).filter_by(application_id=app_a.id).count() == 1
    assert db.query(EEOResponse).filter_by(application_id=app_b.id).count() == 0


def test_unknown_or_missing_token_404(client, db):
    headers, email = auth_headers(client)
    _app(db, _org_id(db, email), eeo_token="eeo_real")
    db.commit()
    assert client.post("/api/v1/public/eeo/eeo_nope", json={"gender": "x"}).status_code == 404


def test_public_eeo_flag_off_503(client, db, monkeypatch):
    headers, email = auth_headers(client)
    _app(db, _org_id(db, email), eeo_token="eeo_off")
    db.commit()
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", False)
    assert client.post("/api/v1/public/eeo/eeo_off", json={"gender": "x"}).status_code == 503


# --- owner-only report + suppression over HTTP --------------------------- #

def test_eeo_report_is_owner_only(client, db):
    headers, email = auth_headers(client)
    assert client.get("/api/v1/compliance/eeo-report", headers=headers).status_code == 200
    caller = db.query(User).filter(User.email == email).first()
    caller.role = "member"
    db.commit()
    assert client.get("/api/v1/compliance/eeo-report", headers=headers).status_code == 403


def test_eeo_report_suppresses_small_cells(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = Role(organization_id=org_id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    for _ in range(5):
        record_response(db, org_id, _app(db, org_id, role.id).id, gender="female")
    record_response(db, org_id, _app(db, org_id, role.id).id, gender="male")
    db.commit()

    rep = client.get("/api/v1/compliance/eeo-report", headers=headers).json()
    assert rep["gender"]["female"] == 5   # at threshold — real count
    assert rep["gender"]["male"] == "<5"  # below threshold — suppressed
