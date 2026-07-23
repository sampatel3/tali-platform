"""retroactive_eval: per-pattern disagreement labels."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.decision_policy.retroactive_eval import disagreement_for_manual_event
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation

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
        "role_id": app.role_id,
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


def test_event_without_logical_role_is_not_relabelled_from_physical_owner(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    app = _make_app(db, org=org, role=role, pre_screen_score_100=80.0)
    event = _ev(
        db,
        app=app,
        event_type="assessment_invite_sent",
        role_id=None,
    )

    assert disagreement_for_manual_event(db, event=event) is None


def test_retroactive_feedback_uses_event_logical_role_score_and_state(db):
    """One physical candidate produces independent owner/related feedback."""

    org = make_org(db, default_score_threshold=65)
    owner = make_role(db, org=org, name="ATS owner")
    related = Role(
        organization_id=org.id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    bootstrap(db, org)

    app = _make_app(
        db,
        org=org,
        role=owner,
        pre_screen_score_100=90.0,
        role_fit_score_cache_100=90.0,
        cv_match_score=90.0,
        application_outcome="hired",
    )
    app.cv_match_details = {"role_fit_score": 90.0}
    db.add(
        SisterRoleEvaluation(
            organization_id=org.id,
            role_id=related.id,
            candidate_id=app.candidate_id,
            source_application_id=app.id,
            ats_application_id=app.id,
            status="done",
            pipeline_stage="applied",
            application_outcome="rejected",
            membership_source="ground_truth_eval",
            spec_fingerprint="related-policy-feedback",
            role_fit_score=5.0,
            details={"role_fit_score": 5.0},
        )
    )
    db.flush()

    owner_event = _ev(db, app=app, event_type="assessment_invite_sent")
    related_event = _ev(
        db,
        app=app,
        event_type="assessment_invite_sent",
        role_id=related.id,
    )

    owner_feedback = disagreement_for_manual_event(db, event=owner_event)
    related_feedback = disagreement_for_manual_event(db, event=related_event)

    assert owner_feedback is not None
    assert owner_feedback.pattern == "agreement"
    assert related_feedback is not None
    assert related_feedback.pattern == "manual-send-on-would-reject"


def test_retroactive_feedback_follows_candidate_after_membership_source_changes(db):
    """Historical physical evidence cannot orphan role/candidate feedback."""

    org = make_org(db, default_score_threshold=65)
    owner = make_role(db, org=org, name="ATS owner")
    related = Role(
        organization_id=org.id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    bootstrap(db, org)
    owner_application = _make_app(
        db,
        org=org,
        role=owner,
        pre_screen_score_100=90.0,
        role_fit_score_cache_100=90.0,
    )
    owner_application.cv_match_details = {"role_fit_score": 90.0}
    direct_application = CandidateApplication(
        organization_id=org.id,
        candidate_id=owner_application.candidate_id,
        role_id=related.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="system",
        application_outcome="open",
    )
    db.add(direct_application)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            SisterRoleEvaluation(
                organization_id=org.id,
                role_id=related.id,
                candidate_id=owner_application.candidate_id,
                source_application_id=owner_application.id,
                ats_application_id=owner_application.id,
                status="done",
                pipeline_stage="review",
                application_outcome="open",
                membership_source="legacy_compat_shadow",
                spec_fingerprint="retroactive-old-source",
                role_fit_score=90.0,
                deleted_at=now - timedelta(minutes=1),
            ),
            SisterRoleEvaluation(
                organization_id=org.id,
                role_id=related.id,
                candidate_id=owner_application.candidate_id,
                source_application_id=direct_application.id,
                ats_application_id=owner_application.id,
                status="done",
                pipeline_stage="applied",
                application_outcome="open",
                membership_source="direct",
                spec_fingerprint="retroactive-current-source",
                role_fit_score=5.0,
                details={"role_fit_score": 5.0},
            ),
        ]
    )
    db.flush()
    event = _ev(
        db,
        app=owner_application,
        event_type="assessment_invite_sent",
        role_id=related.id,
    )

    result = disagreement_for_manual_event(db, event=event)

    assert result is not None
    assert result.pattern == "manual-send-on-would-reject"
