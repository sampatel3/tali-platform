"""Redundant-cycle gate for the autonomous cohort loop.

The bulk-deterministic pass queues every clear verdict each tick; the Sonnet/
Haiku ``run_cycle`` only adds value when it can resolve a NEW escalation. The
gate skips the LLM cycle when the previous one succeeded with zero decisions and
nothing in the cohort changed since — with a force-run backstop so a missed
yield is delayed (≤N h), never lost. Verified against 30d of prod runs before
shipping; these tests pin the decision logic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.tasks.agent_tasks import _redundant_cycle_gate


def _seed_role(db):
    org = Organization(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=5000, job_spec_text="hire",
    )
    db.add(role); db.flush()
    return org, role


def _seed_app(db, org, role, *, stamp):
    cand = Candidate(organization_id=org.id, email=f"{uuid.uuid4().hex[:8]}@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual", cv_text="cv", cv_match_score=70.0,
        updated_at=stamp, score_cached_at=stamp,
        pipeline_stage_updated_at=stamp, application_outcome_updated_at=stamp,
    )
    db.add(app); db.flush()
    return app


def _seed_run(db, org, role, *, started_at, decisions=0, status="succeeded",
              model_version="claude-sonnet-4-5", trigger="cron"):
    run = AgentRun(
        organization_id=org.id, role_id=role.id, trigger=trigger,
        status=status, started_at=started_at, decisions_emitted=decisions,
        model_version=model_version,
    )
    db.add(run); db.flush()
    return run


def test_gate_skips_redundant_unchanged_zero_yield(db):
    org, role = _seed_role(db)
    _seed_app(db, org, role, stamp=datetime.now(timezone.utc) - timedelta(minutes=30))
    _seed_run(db, org, role, started_at=datetime.now(timezone.utc) - timedelta(minutes=10), decisions=0)
    out = _redundant_cycle_gate(db, role=role)
    assert out["would_skip"] is True, out


def test_gate_runs_when_prior_cycle_yielded(db):
    org, role = _seed_role(db)
    _seed_app(db, org, role, stamp=datetime.now(timezone.utc) - timedelta(minutes=30))
    _seed_run(db, org, role, started_at=datetime.now(timezone.utc) - timedelta(minutes=10), decisions=2)
    assert _redundant_cycle_gate(db, role=role)["would_skip"] is False


def test_gate_runs_when_prior_cycle_failed(db):
    org, role = _seed_role(db)
    _seed_app(db, org, role, stamp=datetime.now(timezone.utc) - timedelta(minutes=30))
    _seed_run(db, org, role, started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
              decisions=0, status="failed")
    assert _redundant_cycle_gate(db, role=role)["would_skip"] is False


def test_gate_force_runs_when_stale(db):
    org, role = _seed_role(db)
    _seed_app(db, org, role, stamp=datetime.now(timezone.utc) - timedelta(hours=10))
    _seed_run(db, org, role, started_at=datetime.now(timezone.utc) - timedelta(hours=5), decisions=0)
    out = _redundant_cycle_gate(db, role=role)
    assert out["would_skip"] is False
    assert out["reason"] == "force_run_stale"


def test_gate_runs_when_cohort_changed_since_last_cycle(db):
    org, role = _seed_role(db)
    # an application changed AFTER the last cycle started → there may be new work
    _seed_app(db, org, role, stamp=datetime.now(timezone.utc) - timedelta(minutes=2))
    _seed_run(db, org, role, started_at=datetime.now(timezone.utc) - timedelta(minutes=10), decisions=0)
    out = _redundant_cycle_gate(db, role=role)
    assert out["would_skip"] is False
    assert out["reason"] == "cohort_changed"


def test_gate_runs_when_no_prior_cycle(db):
    org, role = _seed_role(db)
    _seed_app(db, org, role, stamp=datetime.now(timezone.utc) - timedelta(minutes=30))
    assert _redundant_cycle_gate(db, role=role)["would_skip"] is False


def test_gate_ignores_bulk_deterministic_runs(db):
    org, role = _seed_role(db)
    _seed_app(db, org, role, stamp=datetime.now(timezone.utc) - timedelta(minutes=30))
    # only the no-LLM bulk pass has run — that is NOT a prior LLM cycle
    _seed_run(db, org, role, started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
              decisions=5, model_version="bulk-deterministic")
    out = _redundant_cycle_gate(db, role=role)
    assert out["would_skip"] is False
    assert out["reason"] == "no_prior_llm_cycle"
