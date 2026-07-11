"""Native public apply on job pages — flag gate, knockout auto-reject (decision
surfaces), idempotency + race, rate limit, resume/scoring wiring, no knockout
leak in the public payload."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.job_pages import routes as jp_routes
from app.domains.job_pages.screening_service import create_role_question
from app.models import (
    AgentDecision,
    Candidate,
    CandidateApplication,
    CandidateApplicationEvent,
    DisqualificationReason,
    JobPage,
    Organization,
    Role,
    RoleBrief,
)
from app.platform.config import settings
from app.services import rate_limit
from app.services.rate_limit import reset_memory_buckets


@pytest.fixture(autouse=True)
def _enable_apply(monkeypatch):
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", True)
    monkeypatch.setattr(settings, "ATS_APPLY_RATE_LIMIT_PER_HOUR", 20)
    # Deterministic in-proc limiter — don't depend on an ambient Redis.
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: None)
    reset_memory_buckets()
    yield
    reset_memory_buckets()


def _published_page(db, *, slug="acme", token=None):
    token = token or f"tok-{slug}"
    org = Organization(name=slug.title(), slug=slug)
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="Staff Engineer", source="manual",
        job_spec_text="Build things.",
    )
    db.add(role)
    db.flush()
    brief = RoleBrief(organization_id=org.id, role_id=role.id)
    db.add(brief)
    db.flush()
    page = JobPage(
        organization_id=org.id, brief_id=brief.id, token=token, status="open"
    )
    db.add(page)
    db.flush()
    return org, role, page


def _url(page):
    return f"/api/v1/public/job-pages/{page.token}/apply"


def test_apply_creates_candidate_and_application(client, db):
    org, role, page = _published_page(db)
    db.commit()
    r = client.post(_url(page), data={"full_name": "Casey R", "email": "casey@x.test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "received" and "application_id" in body
    # No knockout detail leaks either way.
    assert "knockout_passed" not in body and "failed_question_ids" not in body
    assert "created" not in body

    db.expire_all()
    app = db.query(CandidateApplication).filter_by(id=body["application_id"]).first()
    assert app.source == "careers" and app.application_outcome == "open"
    assert app.source_strategy == "inbound"
    assert db.query(Candidate).filter_by(email="casey@x.test").count() == 1


def test_apply_is_idempotent_per_candidate_role(client, db):
    org, role, page = _published_page(db, slug="idem")
    db.commit()
    first = client.post(_url(page), data={"full_name": "A", "email": "a@x.test"}).json()
    second = client.post(_url(page), data={"full_name": "A", "email": "a@x.test"}).json()
    assert first["application_id"] == second["application_id"]
    db.expire_all()
    assert db.query(CandidateApplication).count() == 1


def test_knockout_failure_queues_decision_on_hub(client, db):
    org, role, page = _published_page(db, slug="ko")
    create_role_question(
        db, org.id, role.id,
        prompt="Are you authorized to work locally?", kind="boolean",
        required=True, knockout=True, knockout_expected=[True],
    )
    reason = DisqualificationReason(
        organization_id=org.id, label="Missing required skills",
        category="we_rejected", position=1, is_active=True,
    )
    db.add(reason)
    db.commit()
    reason_id = reason.id

    r = client.post(
        _url(page), data={"full_name": "B", "email": "b@x.test", "answers": "{}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Generic response — the applicant is never told they failed a knockout.
    assert body["status"] == "received"
    assert "failed_question_ids" not in body and "knockout_passed" not in body

    db.expire_all()
    app = db.query(CandidateApplication).filter_by(id=body["application_id"]).first()
    # Deterministic-reject pattern: outcome stays open; a pending decision surfaces.
    assert app.application_outcome == "open"
    assert app.auto_reject_state == "awaiting_recruiter_approval"
    assert app.auto_reject_reason == "Missing required skills"

    decision = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == app.id, AgentDecision.status == "pending")
        .first()
    )
    assert decision is not None
    assert decision.decision_type == "skip_assessment_reject"
    assert decision.evidence.get("source") == "knockout_screening"
    assert decision.evidence.get("disqualification_reason_id") == reason_id

    event = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "agent_decision_queued",
        )
        .first()
    )
    assert event is not None and event.actor_type == "system"


def test_apply_flag_off_returns_503(client, db, monkeypatch):
    org, role, page = _published_page(db, slug="off")
    db.commit()
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", False)
    r = client.post(_url(page), data={"full_name": "C", "email": "c@x.test"})
    assert r.status_code == 503


def test_apply_requires_contact(client, db):
    org, role, page = _published_page(db, slug="contact")
    db.commit()
    assert client.post(_url(page), data={"full_name": "C"}).status_code == 422


def test_apply_unknown_job_404(client, db):
    _published_page(db, slug="known")
    db.commit()
    r = client.post(
        "/api/v1/public/job-pages/nope/apply",
        data={"full_name": "C", "email": "c@x.test"},
    )
    assert r.status_code == 404


def test_apply_rate_limited(client, db, monkeypatch):
    org, role, page = _published_page(db, slug="rl")
    db.commit()
    monkeypatch.setattr(settings, "ATS_APPLY_RATE_LIMIT_PER_HOUR", 2)
    reset_memory_buckets()
    codes = [
        client.post(
            _url(page), data={"full_name": f"N{i}", "email": f"n{i}@x.test"}
        ).status_code
        for i in range(3)
    ]
    assert codes[:2] == [200, 200]
    assert codes[2] == 429


def test_apply_double_submit_race_recovers(client, db, monkeypatch):
    """A concurrent insert wins the unique (candidate, role) race — the route
    catches IntegrityError and returns the idempotent success, not a 500."""
    org, role, page = _published_page(db, slug="race")
    cand = Candidate(organization_id=org.id, email="race@x.test", full_name="Race")
    db.add(cand)
    db.flush()
    existing = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied", application_outcome="open",
        source="careers",
    )
    db.add(existing)
    db.commit()
    existing_id = existing.id

    def _boom(*a, **k):
        raise IntegrityError("dup", None, Exception("unique"))

    monkeypatch.setattr(jp_routes, "submit_application", _boom)
    r = client.post(_url(page), data={"full_name": "Race", "email": "race@x.test"})
    assert r.status_code == 200, r.text
    assert r.json()["application_id"] == existing_id


def test_resume_triggers_storage_and_scoring(client, db, monkeypatch):
    org, role, page = _published_page(db, slug="resume")
    db.commit()

    from app.services import application_events, document_service

    monkeypatch.setattr(
        document_service, "process_document_upload",
        lambda **k: {
            "file_url": "s3://bucket/cv.pdf",
            "filename": "cv.pdf",
            "extracted_text": "Jane the engineer",
            "text_preview": "Jane the engineer",
        },
    )
    # Skip the real hygiene reload (returns None -> hygiene no-op).
    monkeypatch.setattr(document_service, "load_stored_document_bytes", lambda url: None)
    calls = {}
    monkeypatch.setattr(
        application_events, "on_application_created",
        lambda app, **k: calls.update({"app_id": app.id, "kwargs": k}),
    )

    r = client.post(
        _url(page),
        data={"full_name": "Jane", "email": "jane@x.test"},
        files={"resume": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 200, r.text
    app_id = r.json()["application_id"]
    db.expire_all()
    app = db.query(CandidateApplication).filter_by(id=app_id).first()
    assert app.cv_file_url == "s3://bucket/cv.pdf" and app.cv_filename == "cv.pdf"
    assert app.candidate.cv_file_url == "s3://bucket/cv.pdf"
    # Normal scoring flow triggered for the new application.
    assert calls.get("app_id") == app_id
    assert calls["kwargs"].get("score") is True


def test_resume_rejects_wrong_type(client, db):
    org, role, page = _published_page(db, slug="badtype")
    db.commit()
    r = client.post(
        _url(page),
        data={"full_name": "Jane", "email": "jane@x.test"},
        files={"resume": ("virus.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 422


def test_public_payload_hides_knockout_fields(client, db):
    org, role, page = _published_page(db, slug="leak")
    create_role_question(
        db, org.id, role.id,
        prompt="Authorized to work locally?", kind="boolean",
        required=True, knockout=True, knockout_expected=[True],
    )
    db.commit()
    r = client.get(f"/api/v1/public/job/{page.token}")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["accepts_applications"] is True
    qs = payload["screening_questions"]
    assert len(qs) == 1
    q = qs[0]
    assert q["prompt"] == "Authorized to work locally?" and q["required"] is True
    # The passing answer must never leak.
    assert "knockout" not in q and "knockout_expected" not in q
