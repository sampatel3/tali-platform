"""retroactive_eval: per-pattern disagreement labels."""

from __future__ import annotations

from datetime import datetime, timezone

from app.decision_policy.retroactive_eval import disagreement_for_manual_event
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent

from .conftest import bootstrap, make_org, make_role


def _make_app(db, *, org, role, **fields) -> CandidateApplication:
    candidate = Candidate(
        organization_id=org.id, email=f"r{id(db)}@x.test", full_name="R"
    )
    db.add(candidate)
    db.flush()
    body = {
        "organization_id": org.id,
        "candidate_id": candidate.id,
        "role_id": role.id,
        "status": "applied",
        "pipeline_stage": "review",
        "pipeline_stage_source": "recruiter",
    }
    body.update(fields)
    app = CandidateApplication(**body)
    db.add(app)
    db.flush()
    return app


def _ev(db, *, app, event_type, **fields) -> CandidateApplicationEvent:
    body = {
        "application_id": app.id,
        "organization_id": app.organization_id,
        "event_type": event_type,
        "actor_type": "recruiter",
        "actor_id": 1,
    }
    body.update(fields)
    ev = CandidateApplicationEvent(**body)
    db.add(ev)
    db.flush()
    return ev


def test_recruiter_send_when_policy_would_send_is_agreement(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    app = _make_app(db, org=org, role=role, pre_screen_score_100=80.0)
    app.cv_match_details = {"role_fit_score": 80.0}
    db.flush()
    ev = _ev(db, app=app, event_type="assessment_invite_sent")
    out = disagreement_for_manual_event(db, event=ev)
    assert out is not None
    assert out.pattern == "agreement"


def test_recruiter_send_when_policy_would_reject_flags_pattern(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    app = _make_app(db, org=org, role=role, pre_screen_score_100=5.0)
    app.cv_match_details = {"role_fit_score": 5.0}
    db.flush()
    ev = _ev(db, app=app, event_type="assessment_invite_sent")
    out = disagreement_for_manual_event(db, event=ev)
    assert out is not None
    assert out.pattern == "manual-send-on-would-reject"


def test_recruiter_reject_when_policy_would_send_flags_pattern(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    app = _make_app(db, org=org, role=role, pre_screen_score_100=80.0)
    app.cv_match_details = {"role_fit_score": 80.0}
    db.flush()
    ev = _ev(
        db,
        app=app,
        event_type="application_outcome_changed",
        to_outcome="rejected",
    )
    out = disagreement_for_manual_event(db, event=ev)
    assert out is not None
    assert out.pattern == "manual-reject-on-would-send"


def test_non_recruiter_event_returns_none(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    app = _make_app(db, org=org, role=role)
    ev = _ev(db, app=app, event_type="cv_scored", actor_type="agent")
    out = disagreement_for_manual_event(db, event=ev)
    assert out is None
