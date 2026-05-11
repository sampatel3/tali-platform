"""Evidence validation on AgentDecision (PR 3 governance).

Verifies that ``validate_agent_decision_evidence`` correctly classifies
agent-supplied evidence as ``passed`` / ``failed`` / ``skipped`` and
that ``queue_decision.run`` persists the outcome onto the new
``validation_status`` and ``validation_failures`` columns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import event

from app.actions import Actor, queue_decision
from app.agent_runtime.decision_evidence import (
    VALIDATION_STATUS_FAILED,
    VALIDATION_STATUS_PASSED,
    VALIDATION_STATUS_SKIPPED,
    validate_agent_decision_evidence,
)
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


# SQLite doesn't autoincrement BigInteger PKs; mirror the hook from
# test_agent_runtime_tools so AgentRun/AgentDecision get ids.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0, "agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)
event.listen(AgentDecision, "before_insert", _assign_big_pk)


def _make_org(db) -> Organization:
    org = Organization(name="Evidence Org", slug=f"evidence-org-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(db, org: Organization) -> Role:
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        source="manual",
        agentic_mode_enabled=True,
        auto_promote=True,
    )
    db.add(role)
    db.flush()
    return role


def _make_application(
    db,
    *,
    org: Organization,
    role: Role,
    cv_text: str = "",
    cv_match_score: float | None = None,
    pre_screen_score: float | None = None,
    taali_score: float | None = None,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=org.id,
        email=f"c-{id(db)}-{role.id}@example.com",
        full_name="Test Candidate",
        cv_text=cv_text,
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text=cv_text,
        cv_match_score=cv_match_score,
        pre_screen_score_100=pre_screen_score,
        taali_score_cache_100=taali_score,
    )
    db.add(app)
    db.flush()
    return app


def _make_agent_run(db, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
        model_version="haiku-test",
        prompt_version="agent.test",
    )
    db.add(run)
    db.flush()
    return run


def _queue_decision_with_evidence(
    db, *, org, role, app, run, evidence
) -> AgentDecision:
    return queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Test evidence pathway.",
        evidence=evidence,
        confidence=0.9,
        model_version="haiku-test",
        prompt_version="agent.test",
    )


# ---------------------------------------------------------------------------
# Standalone validator behaviour
# ---------------------------------------------------------------------------


def test_validator_skips_when_evidence_empty(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence=None,
        model_version="m",
        prompt_version="p",
        idempotency_key="k",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_SKIPPED
    assert outcome.checks_run == 0


def test_validator_passes_when_cited_scores_match_application(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(
        db,
        org=org,
        role=role,
        cv_match_score=78.5,
        pre_screen_score=82.0,
        taali_score=85.0,
    )
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence={
            "cv_match_score": 78.5,
            "pre_screen_score": 82.0,
            "taali_score": 85.0,
        },
        model_version="m",
        prompt_version="p",
        idempotency_key="k-pass-scores",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_PASSED
    assert outcome.failures == []
    assert outcome.checks_run == 3


def test_validator_fails_when_cited_score_does_not_match(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, cv_match_score=50.0)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence={"cv_match_score": 95.0},  # Agent fabricated a higher score
        model_version="m",
        prompt_version="p",
        idempotency_key="k-fail-scores",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_FAILED
    assert outcome.checks_run == 1
    assert any("cv_match_score" in msg for msg in outcome.failures)


def test_validator_passes_when_cv_excerpt_appears_in_cv_text(db):
    org = _make_org(db)
    role = _make_role(db, org)
    cv = (
        "Senior backend engineer with 8 years building distributed systems. "
        "Led migration of monolith to microservices on AWS."
    )
    app = _make_application(db, org=org, role=role, cv_text=cv)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence={
            "cv_excerpts": [
                {"quoted_text": "8 years building distributed systems"}
            ]
        },
        model_version="m",
        prompt_version="p",
        idempotency_key="k-pass-excerpt",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_PASSED
    assert outcome.failures == []
    assert outcome.checks_run == 1


def test_validator_fails_when_cv_excerpt_fabricated(db):
    org = _make_org(db)
    role = _make_role(db, org)
    cv = "Senior backend engineer with 8 years building distributed systems."
    app = _make_application(db, org=org, role=role, cv_text=cv)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence={
            "cv_excerpts": [
                {
                    "quoted_text": "PhD in computer science from Stanford with 15 publications in NeurIPS"
                }
            ]
        },
        model_version="m",
        prompt_version="p",
        idempotency_key="k-fail-excerpt",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_FAILED
    assert outcome.checks_run == 1
    assert any("cv_excerpt" in msg for msg in outcome.failures)


def test_validator_accepts_string_excerpt(db):
    """Backward compat: evidence may carry a bare string instead of a dict."""
    org = _make_org(db)
    role = _make_role(db, org)
    cv = "Built ETL pipelines in Apache Airflow processing 2TB nightly."
    app = _make_application(db, org=org, role=role, cv_text=cv)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence={"cv_excerpt": "Built ETL pipelines in Apache Airflow"},
        model_version="m",
        prompt_version="p",
        idempotency_key="k-string-excerpt",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_PASSED


def test_validator_tolerates_small_score_drift(db):
    """Agent may round scores; difference within 0.5 should pass."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, taali_score=78.4)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence={"taali_score": 78.0},
        model_version="m",
        prompt_version="p",
        idempotency_key="k-drift",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_PASSED


def test_validator_supports_cited_scores_nested_shape(db):
    """Evidence dict ``{"cited_scores": {"cv_match_score": ...}}`` is also
    recognised (matches the structured-citation pattern PR 3 documents)."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, cv_match_score=66.0)
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        reasoning="x",
        evidence={"cited_scores": {"cv_match_score": 66.0}},
        model_version="m",
        prompt_version="p",
        idempotency_key="k-nested",
    )
    db.add(decision)
    db.flush()

    outcome = validate_agent_decision_evidence(decision, db)
    assert outcome.status == VALIDATION_STATUS_PASSED
    assert outcome.checks_run == 1


# ---------------------------------------------------------------------------
# Integration: queue_decision.run persists validation outcome
# ---------------------------------------------------------------------------


def test_queue_decision_persists_passed_validation(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, cv_match_score=78.0)
    run = _make_agent_run(db, role)

    decision = _queue_decision_with_evidence(
        db,
        org=org,
        role=role,
        app=app,
        run=run,
        evidence={"cv_match_score": 78.0},
    )
    db.refresh(decision)
    assert decision.validation_status == VALIDATION_STATUS_PASSED
    assert decision.validation_failures is None


def test_queue_decision_persists_failed_validation_but_still_queues(db):
    """A failed validation must NOT refuse to queue — the decision lands,
    but the badge surfaces the failure to the recruiter."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, cv_match_score=40.0)
    run = _make_agent_run(db, role)

    decision = _queue_decision_with_evidence(
        db,
        org=org,
        role=role,
        app=app,
        run=run,
        evidence={"cv_match_score": 99.0},
    )
    db.refresh(decision)
    assert decision.id is not None
    assert decision.status == "pending"  # Still queued.
    assert decision.validation_status == VALIDATION_STATUS_FAILED
    assert decision.validation_failures
    assert any("cv_match_score" in msg for msg in decision.validation_failures)


def test_queue_decision_persists_skipped_when_no_evidence(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    decision = _queue_decision_with_evidence(
        db,
        org=org,
        role=role,
        app=app,
        run=run,
        evidence=None,
    )
    db.refresh(decision)
    assert decision.validation_status == VALIDATION_STATUS_SKIPPED
    assert decision.validation_failures is None
