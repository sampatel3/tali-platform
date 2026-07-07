"""P1: public careers apply — flag gate, knockout, idempotency, rate limit."""
import pytest

from app.domains.assessments_runtime.screening_service import create_role_question
from app.models import Candidate, CandidateApplication, Organization, Role
from app.models.role import ROLE_STATUS_PUBLISHED
from app.platform.config import settings
from app.services import rate_limit
from app.services.rate_limit import reset_memory_buckets


@pytest.fixture(autouse=True)
def _enable_apply(monkeypatch):
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", True)
    monkeypatch.setattr(settings, "ATS_APPLY_RATE_LIMIT_PER_HOUR", 20)
    # Force the deterministic in-proc limiter — don't depend on an ambient Redis
    # (which persists counts across tests and windows).
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: None)
    reset_memory_buckets()
    yield
    reset_memory_buckets()


def _published_role(db, *, slug="careers-org", role_slug="staff-eng"):
    org = Organization(name="Acme", slug=slug)
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="Staff Engineer", source="manual",
        status=ROLE_STATUS_PUBLISHED, slug=role_slug,
    )
    db.add(role)
    db.flush()
    return org, role


def _apply_url(org, role):
    return f"/careers/v1/{org.slug}/jobs/{role.slug}/apply"


def test_apply_creates_candidate_and_application(client, db):
    org, role = _published_role(db)
    db.commit()
    r = client.post(_apply_url(org, role), json={"full_name": "Casey R", "email": "casey@x.test"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True and body["knockout_passed"] is True

    app = db.query(CandidateApplication).filter_by(id=body["application_id"]).first()
    assert app.source == "careers" and app.application_outcome == "open"
    assert db.query(Candidate).filter_by(email="casey@x.test").count() == 1


def test_apply_is_idempotent_per_candidate_role(client, db):
    org, role = _published_role(db, slug="idem-org")
    db.commit()
    first = client.post(_apply_url(org, role), json={"full_name": "A", "email": "a@x.test"}).json()
    second = client.post(_apply_url(org, role), json={"full_name": "A", "email": "a@x.test"}).json()
    assert second["created"] is False
    assert first["application_id"] == second["application_id"]


def test_knockout_failure_auto_rejects(client, db):
    org, role = _published_role(db, slug="ko-org")
    create_role_question(
        db, org.id, role.id,
        prompt="Are you authorized to work locally?", kind="boolean",
        required=True, knockout=True, knockout_expected=[True],
    )
    db.commit()
    # Answers the knockout with the wrong value.
    r = client.post(
        _apply_url(org, role),
        json={"full_name": "B", "email": "b@x.test", "answers": {}},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["knockout_passed"] is False and body["failed_question_ids"]
    app = db.query(CandidateApplication).filter_by(id=body["application_id"]).first()
    assert app.application_outcome == "rejected" and app.disposition_category == "we_rejected"


def test_apply_requires_contact_and_gate_and_published(client, db, monkeypatch):
    org, role = _published_role(db, slug="gate-org")
    db.commit()

    # No email/phone -> 422.
    assert client.post(_apply_url(org, role), json={"full_name": "C"}).status_code == 422

    # Unknown job -> 404.
    assert client.post(
        f"/careers/v1/{org.slug}/jobs/nope/apply",
        json={"full_name": "C", "email": "c@x.test"},
    ).status_code == 404

    # Flag off -> 503.
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", False)
    assert client.post(
        _apply_url(org, role), json={"full_name": "C", "email": "c@x.test"}
    ).status_code == 503


def test_apply_rate_limited(client, db, monkeypatch):
    org, role = _published_role(db, slug="rl-org")
    db.commit()
    monkeypatch.setattr(settings, "ATS_APPLY_RATE_LIMIT_PER_HOUR", 2)
    reset_memory_buckets()
    codes = [
        client.post(_apply_url(org, role), json={"full_name": f"N{i}", "email": f"n{i}@x.test"}).status_code
        for i in range(3)
    ]
    assert codes[:2] == [201, 201]
    assert codes[2] == 429
