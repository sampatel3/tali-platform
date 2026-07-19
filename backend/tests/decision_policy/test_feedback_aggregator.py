"""Aggregator pulls all three signal sources."""

from __future__ import annotations

from datetime import datetime, timezone

from app.decision_policy.feedback_aggregator import (
    DEFAULT_SIGNAL_WEIGHTS,
    aggregate_signals,
)
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.decision_feedback import DecisionFeedback
from app.models.user import User

from .conftest import bootstrap, make_org, make_role


def _make_user(db, *, organization_id: int) -> User:
    user = User(
        organization_id=organization_id,
        email=f"u{id(db)}@x.test",
        full_name="U",
        hashed_password="x",
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_application(db, *, org, role) -> CandidateApplication:
    candidate = Candidate(
        organization_id=org.id, email=f"c{id(db)}@x.test", full_name="C"
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        pre_screen_score_100=80.0,
        cv_match_details={"role_fit_score": 85.0},
    )
    db.add(app)
    db.flush()
    return app


def _make_decision(db, *, org, role, app, agent_run) -> AgentDecision:
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=agent_run.id,
        decision_type="advance_to_interview",
        recommendation="advance",
        status="overridden",
        reasoning="agent thought advance",
        evidence={},
        confidence=0.7,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"k-{id(db)}-{app.id}",
        human_disposition="overridden",
        resolved_at=datetime.now(timezone.utc),
    )
    db.add(decision)
    db.flush()
    return decision


def test_aggregator_pulls_three_sources(db):
    org = make_org(db)
    role = make_role(db, org=org)
    bootstrap(db, org)
    user = _make_user(db, organization_id=int(org.id))
    app = _make_application(db, org=org, role=role)

    # Source 2 first so we have a decision id to FK off in source 1.
    run = AgentRun(
        organization_id=org.id,
        role_id=role.id,
        trigger="manual",
        status="succeeded",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    decision_for_override = _make_decision(db, org=org, role=role, app=app, agent_run=run)

    # Source 1: explicit teach (FK to a real AgentDecision).
    fb = DecisionFeedback(
        organization_id=org.id,
        decision_id=int(decision_for_override.id),
        reviewer_id=user.id,
        role_id=role.id,
        failure_mode="wrong_threshold",
        correction_text="too strict",
        scope="role",
    )
    db.add(fb)
    db.flush()
    # Mark the same decision's feedback_id so the silent-override
    # path skips it (it's a teach + override on the same row, teach
    # signal wins).
    # Leave feedback_id null instead so override still surfaces.

    # Source 3: manual recruiter event (assessment send) — but the
    # current policy would queue send too, so it should be agreement
    # → no signal.
    ev = CandidateApplicationEvent(
        application_id=app.id,
        organization_id=org.id,
        event_type="assessment_invite_sent",
        actor_type="recruiter",
        actor_id=int(user.id),
    )
    db.add(ev)
    db.flush()

    agg = aggregate_signals(db, organization_id=int(org.id))
    # Teach + override land; manual event would agree → not counted.
    sigs_by_type = {s.signal_type for s in agg.signals}
    assert "teach" in sigs_by_type
    assert "override" in sigs_by_type
    assert agg.teach_count == 1
    assert agg.override_count == 1


def test_default_weights_used_when_no_org_override(db):
    org = make_org(db)
    role = make_role(db, org=org)
    bootstrap(db, org)
    user = _make_user(db, organization_id=int(org.id))
    app = _make_application(db, org=org, role=role)
    run = AgentRun(
        organization_id=org.id, role_id=role.id, trigger="manual",
        status="succeeded", model_version="m", prompt_version="p",
    )
    db.add(run); db.flush()
    decision = _make_decision(db, org=org, role=role, app=app, agent_run=run)
    fb = DecisionFeedback(
        organization_id=org.id,
        decision_id=int(decision.id),
        reviewer_id=user.id,
        role_id=role.id,
        failure_mode="rubric_mismatch",
        correction_text="...",
        scope="role",
    )
    db.add(fb)
    db.flush()
    agg = aggregate_signals(db, organization_id=int(org.id))
    teach_signal = next(s for s in agg.signals if s.signal_type == "teach")
    assert teach_signal.weight == DEFAULT_SIGNAL_WEIGHTS["teach"]


def test_unsigned_org_scope_feedback_skipped(db):
    org = make_org(db)
    role = make_role(db, org=org)
    bootstrap(db, org)
    user = _make_user(db, organization_id=int(org.id))
    app = _make_application(db, org=org, role=role)
    run = AgentRun(
        organization_id=org.id, role_id=role.id, trigger="manual",
        status="succeeded", model_version="m", prompt_version="p",
    )
    db.add(run); db.flush()
    decision = _make_decision(db, org=org, role=role, app=app, agent_run=run)
    fb = DecisionFeedback(
        organization_id=org.id,
        decision_id=int(decision.id),
        reviewer_id=user.id,
        role_id=None,
        failure_mode="wrong_threshold",
        correction_text="org-wide",
        scope="org",
        cosign_required=True,
        cosigned_at=None,
    )
    db.add(fb)
    db.flush()
    agg = aggregate_signals(db, organization_id=int(org.id))
    assert all(s.signal_type != "teach" for s in agg.signals)
