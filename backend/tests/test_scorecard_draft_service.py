"""Agent-drafted scorecards from a transcript.

The agent authors a DRAFT (recommendation + 5-Ds ratings + grounded notes) from
the interview transcript; the human edits and submits it. These cover the pure
mapping, the on-demand endpoint, idempotent re-drafting, the submitted-card
guard (a submitted card is never overwritten), the no-transcript error, auth,
and that the LLM call goes through the metered structured path (mocked — no real
API). The blank-form + submit lifecycle stays in test_interview_scorecards.py.
"""

from datetime import datetime, timezone

import pytest

from app.llm.structured import StructuredResult
from app.models.application_interview import ApplicationInterview
from app.models.interview_feedback import InterviewFeedback
from app.models.user import User
from app.services import scorecard_draft_service as svc
from app.services.scorecard_draft_service import (
    ScorecardDraftExtraction,
    SubmittedCardError,
    apply_scorecard_draft,
    build_scorecard_messages,
    select_draftable_interview,
)
from tests.conftest import auth_headers


def _create_application(client, headers, candidate_email="draft@example.com"):
    role = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer", "description": "Hiring"},
        headers=headers,
    ).json()
    app_resp = client.post(
        f"/api/v1/roles/{role['id']}/applications",
        json={"candidate_email": candidate_email, "candidate_name": "Draft Candidate"},
        headers=headers,
    )
    assert app_resp.status_code == 201, app_resp.text
    return app_resp.json()


def _interview(db, org_id, application_id, transcript="Interviewer: tell me. Candidate: I led the migration."):
    iv = ApplicationInterview(
        organization_id=org_id,
        application_id=application_id,
        stage="interview",
        source="fireflies",
        status="completed",
        transcript_text=transcript,
        summary="A screening call.",
    )
    db.add(iv)
    db.commit()
    return iv


def _canned_extraction():
    return ScorecardDraftExtraction(
        overall_recommendation="yes",
        overall_rating=3,
        dimension_ratings={"delegation": 4, "deliverable": 3, "bogus_axis": 5, "diligence": 9},
        competencies=[{"name": "Delegation", "rating": 4, "comment": "Led a migration."}],
        notes="Strong on ownership: 'I led the migration.'",
    )


# --------------------------------------------------------------------------
# Pure pieces.
# --------------------------------------------------------------------------
def test_select_draftable_interview_prefers_latest_with_transcript(db):
    class _App:
        pass

    old = ApplicationInterview(transcript_text="a", meeting_date=datetime(2024, 1, 1, tzinfo=timezone.utc))
    new = ApplicationInterview(transcript_text="b", meeting_date=datetime(2024, 6, 1, tzinfo=timezone.utc))
    no_text = ApplicationInterview(transcript_text="   ")
    app = _App()
    app.interviews = [old, new, no_text]
    assert select_draftable_interview(app) is new
    # A transcript-less interview is never draftable.
    app.interviews = [no_text]
    assert select_draftable_interview(app) is None


def test_build_scorecard_messages_embeds_transcript_and_axes():
    system, messages = build_scorecard_messages(
        role_name="Backend Engineer",
        candidate_name="Ada",
        transcript_text="Candidate: I shipped it.",
        summary="short",
    )
    assert "scorecard" in system.lower()
    body = messages[0]["content"]
    assert "Backend Engineer" in body and "Ada" in body
    assert "I shipped it." in body
    assert "delegation" in body.lower() and "deliverable" in body.lower()


def test_apply_scorecard_draft_maps_and_cleans(db):
    # Build a minimal app + interview + user directly (no LLM, no HTTP).
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.organization import Organization
    from app.models.role import Role

    org = Organization(name="Acme", slug="acme-map")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Eng")
    cand = Candidate(email="c@x.io", full_name="C")
    db.add_all([role, cand])
    db.flush()
    app = CandidateApplication(organization_id=org.id, role_id=role.id, candidate_id=cand.id)
    db.add(app)
    db.flush()
    iv = _interview(db, org.id, app.id)

    user = User(email="u@x.io", hashed_password="x", organization_id=org.id, is_active=True)
    db.add(user)
    db.flush()

    card = apply_scorecard_draft(
        db, app=app, interview=iv, interviewer_user_id=user.id, extraction=_canned_extraction()
    )
    db.commit()
    assert card.submitted_at is None  # drafts are never auto-submitted
    assert card.overall_recommendation == "yes"
    assert card.overall_rating == 3
    # Off-axis + out-of-range dimension keys are dropped; valid ones kept.
    assert card.dimension_ratings == {"delegation": 4, "deliverable": 3}
    assert card.competencies[0]["name"] == "Delegation"
    assert card.interviewer_user_id == user.id
    assert card.interview_id == iv.id


# --------------------------------------------------------------------------
# On-demand endpoint — the mocked metered LLM path.
# --------------------------------------------------------------------------
def _mock_llm(monkeypatch, extraction=None, ok=True):
    """Mock the structured LLM so no real API is hit. Asserts the draft path
    calls generate_structured with forced tool-use + the scorecard feature."""
    calls = {}

    def fake_generate_structured(client, **kwargs):
        calls["kwargs"] = kwargs
        return StructuredResult(value=extraction if ok else None, ok=ok, error_reason="" if ok else "boom")

    monkeypatch.setattr(svc, "generate_structured", fake_generate_structured)
    # Fail loudly if the resolver's real client were ever built in a test.
    monkeypatch.setattr(svc, "get_metered_client", lambda **k: object())
    return calls


def test_draft_from_transcript_creates_draft_via_metered_path(client, db, monkeypatch):
    headers, email = auth_headers(client)
    app = _create_application(client, headers, candidate_email="ondemand@example.com")
    aid = app["id"]
    org_id = db.query(User).filter(User.email == email).first().organization_id
    iv = _interview(db, org_id, aid)

    calls = _mock_llm(monkeypatch, extraction=_canned_extraction())
    r = client.post(
        f"/api/v1/applications/{aid}/scorecards/draft-from-transcript",
        json={"interview_id": iv.id},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["submitted_at"] is None  # a DRAFT — never auto-submitted
    assert body["ai_drafted"] is True
    assert body["overall_recommendation"] == "yes"
    assert body["dimension_ratings"] == {"delegation": 4, "deliverable": 3}
    assert body["interview_id"] == iv.id
    # Went through the metered structured path with forced tool-use.
    kwargs = calls["kwargs"]
    assert kwargs["use_tool_use"] is True
    assert kwargs["metering"].feature == "scorecard_draft"
    assert kwargs["metering"].organization_id == org_id


def test_draft_from_transcript_is_idempotent(client, db, monkeypatch):
    headers, email = auth_headers(client)
    app = _create_application(client, headers, candidate_email="idem@example.com")
    aid = app["id"]
    org_id = db.query(User).filter(User.email == email).first().organization_id
    iv = _interview(db, org_id, aid)

    _mock_llm(monkeypatch, extraction=_canned_extraction())
    first = client.post(
        f"/api/v1/applications/{aid}/scorecards/draft-from-transcript",
        json={"interview_id": iv.id},
        headers=headers,
    ).json()
    # Re-draft with a different result → same row edited in place, still a draft.
    _mock_llm(monkeypatch, extraction=ScorecardDraftExtraction(overall_recommendation="strong_yes"))
    second = client.post(
        f"/api/v1/applications/{aid}/scorecards/draft-from-transcript",
        json={"interview_id": iv.id},
        headers=headers,
    ).json()
    assert second["id"] == first["id"]
    assert second["overall_recommendation"] == "strong_yes"
    assert db.query(InterviewFeedback).filter_by(application_id=aid).count() == 1


def test_draft_never_overwrites_a_submitted_card(client, db, monkeypatch):
    headers, email = auth_headers(client)
    app = _create_application(client, headers, candidate_email="submitted@example.com")
    aid = app["id"]
    org_id = db.query(User).filter(User.email == email).first().organization_id
    iv = _interview(db, org_id, aid)
    me = db.query(User).filter(User.email == email).first()

    # A card this interviewer already SUBMITTED — human-owned, untouchable.
    submitted = InterviewFeedback(
        organization_id=org_id,
        application_id=aid,
        role_id=app["role_id"],
        interviewer_user_id=me.id,
        interview_id=iv.id,
        interview_round="interview",
        overall_recommendation="no",
        notes="human wrote this",
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(submitted)
    db.commit()

    called = {"llm": False}

    def fake_generate_structured(client, **kwargs):  # pragma: no cover - must NOT run
        called["llm"] = True
        return StructuredResult(value=_canned_extraction(), ok=True)

    monkeypatch.setattr(svc, "generate_structured", fake_generate_structured)
    monkeypatch.setattr(svc, "get_metered_client", lambda **k: object())

    r = client.post(
        f"/api/v1/applications/{aid}/scorecards/draft-from-transcript",
        json={"interview_id": iv.id},
        headers=headers,
    )
    assert r.status_code == 409, r.text
    assert called["llm"] is False  # guarded BEFORE any billable call
    db.expire_all()
    keep = db.get(InterviewFeedback, submitted.id)
    assert keep.overall_recommendation == "no" and keep.notes == "human wrote this"


def test_draft_from_transcript_no_transcript_clean_error(client, db, monkeypatch):
    headers, _ = auth_headers(client)
    app = _create_application(client, headers, candidate_email="notext@example.com")
    aid = app["id"]
    # No interview linked at all.
    _mock_llm(monkeypatch, extraction=_canned_extraction())
    r = client.post(
        f"/api/v1/applications/{aid}/scorecards/draft-from-transcript",
        json={},
        headers=headers,
    )
    assert r.status_code == 400
    assert "transcript" in r.json()["detail"].lower()


def test_draft_from_transcript_requires_auth(client, db):
    # No auth header → 401 (the authz gate covers every write route).
    r = client.post("/api/v1/applications/1/scorecards/draft-from-transcript", json={})
    assert r.status_code == 401


# --------------------------------------------------------------------------
# Flag-gated auto-draft path (webhook) — default OFF.
# --------------------------------------------------------------------------
def test_autodraft_is_a_noop_when_flag_off(db, monkeypatch):
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.organization import Organization
    from app.models.role import Role

    org = Organization(name="Acme", slug="acme-auto")
    db.add(org)
    db.flush()
    org.fireflies_owner_email = "owner@acme.io"
    role = Role(organization_id=org.id, name="Eng")
    cand = Candidate(email="c2@x.io", full_name="C2")
    db.add_all([role, cand])
    db.flush()
    app = CandidateApplication(organization_id=org.id, role_id=role.id, candidate_id=cand.id)
    db.add(app)
    db.flush()
    iv = _interview(db, org.id, app.id)
    db.add(User(email="owner@acme.io", hashed_password="x", organization_id=org.id, is_active=True))
    db.commit()

    # Flag defaults OFF → no draft, no LLM call.
    monkeypatch.setattr(
        svc, "generate_structured", lambda *a, **k: pytest.fail("LLM must not run when flag off")
    )
    assert svc.maybe_autodraft_from_webhook(db, org=org, app=app, interview=iv) is None
    assert db.query(InterviewFeedback).filter_by(application_id=app.id).count() == 0


def test_autodraft_runs_when_flag_on_and_owner_resolves(db, monkeypatch):
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.organization import Organization
    from app.models.role import Role

    org = Organization(name="Acme", slug="acme-auto2")
    db.add(org)
    db.flush()
    org.fireflies_owner_email = "host@acme.io"
    role = Role(organization_id=org.id, name="Eng")
    cand = Candidate(email="c3@x.io", full_name="C3")
    db.add_all([role, cand])
    db.flush()
    app = CandidateApplication(organization_id=org.id, role_id=role.id, candidate_id=cand.id)
    db.add(app)
    db.flush()
    iv = _interview(db, org.id, app.id)
    owner = User(email="host@acme.io", hashed_password="x", organization_id=org.id, is_active=True)
    db.add(owner)
    db.commit()

    monkeypatch.setattr(svc.settings, "SCORECARD_AUTODRAFT_ENABLED", True)
    _mock_llm(monkeypatch, extraction=_canned_extraction())
    card = svc.maybe_autodraft_from_webhook(db, org=org, app=app, interview=iv)
    db.commit()
    assert card is not None
    assert card.interviewer_user_id == owner.id  # filed under the meeting owner
    assert card.submitted_at is None  # never auto-submitted
    # Re-delivery of the same webhook doesn't re-spend (card already exists).
    monkeypatch.setattr(
        svc, "generate_structured", lambda *a, **k: pytest.fail("must not redraft on re-delivery")
    )
    assert svc.maybe_autodraft_from_webhook(db, org=org, app=app, interview=iv) is None
