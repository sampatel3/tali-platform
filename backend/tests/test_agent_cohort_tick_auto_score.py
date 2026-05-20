"""Auto-scoring is wired into the cohort tick so the agent picks up
new candidates without a recruiter clicking ``Process N candidates``.

We test the helper directly rather than the full Celery task — the task
calls ``run_cycle`` which needs Anthropic, an agent_run table, and lots
of plumbing that's orthogonal to the auto-enqueue we're verifying.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.tasks.agent_tasks import _auto_enqueue_scoring


def _seed_role(db, *, agentic_mode_enabled: bool = True) -> tuple[Organization, Role]:
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        agentic_mode_enabled=agentic_mode_enabled,
        monthly_usd_budget_cents=5000,
        job_spec_text="hire stuff",
    )
    db.add(role); db.flush()
    return org, role


def _seed_app(
    db,
    *,
    org: Organization,
    role: Role,
    cv_text: str | None = "candidate cv text",
    cv_match_score: float | None = None,
    application_outcome: str = "open",
) -> CandidateApplication:
    import uuid
    cand = Candidate(
        organization_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@x.test",
        full_name="C",
    )
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome=application_outcome,
        source="manual",
        cv_text=cv_text,
        cv_match_score=cv_match_score,
    )
    db.add(app); db.flush()
    return app


def test_auto_enqueue_calls_enqueue_score_for_each_unscored_app(db):
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)

    seen_app_ids: list[int] = []

    def fake_enqueue(db_arg, app, *, force=False):
        seen_app_ids.append(int(app.id))
        return object()  # truthy stand-in for a Job

    with patch("app.services.cv_score_orchestrator.enqueue_score", side_effect=fake_enqueue):
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 3
    assert len(seen_app_ids) == 3


def test_auto_enqueue_skips_already_scored_apps(db):
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role, cv_match_score=72.0)  # scored — skip
    _seed_app(db, org=org, role=role)                       # unscored — enqueue
    _seed_app(db, org=org, role=role, cv_match_score=33.0)  # scored — skip

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 1
    assert m.call_count == 1


def test_auto_enqueue_skips_apps_without_cv_text(db):
    """Apps without ``cv_text`` need the CV fetcher first; the scoring
    enqueue helper would just no-op on them. Filter them out upstream
    so we don't churn through them every tick."""
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role, cv_text=None)  # no CV text
    _seed_app(db, org=org, role=role, cv_text="")    # empty CV text
    _seed_app(db, org=org, role=role)                # has CV text — enqueue

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 1
    assert m.call_count == 1


def test_auto_enqueue_skips_rejected_or_hired_apps(db):
    """Only ``outcome='open'`` apps get auto-scored. Closed-out apps
    (rejected, hired, withdrawn) are settled — don't burn budget
    rescoring them."""
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role)                                # open — enqueue
    _seed_app(db, org=org, role=role, application_outcome="rejected")
    _seed_app(db, org=org, role=role, application_outcome="hired")

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 1
    assert m.call_count == 1


def test_auto_enqueue_returns_zero_when_no_apps(db):
    org, role = _seed_role(db)
    count = _auto_enqueue_scoring(db, role=role)
    assert count == 0


def test_auto_enqueue_swallows_individual_failures(db):
    """One app's enqueue raising shouldn't kill the whole batch."""
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)

    calls = {"n": 0}

    def flaky(db_arg, app, *, force=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated metering glitch")
        return object()

    with patch("app.services.cv_score_orchestrator.enqueue_score", side_effect=flaky):
        count = _auto_enqueue_scoring(db, role=role)

    # 2 successful enqueues, 1 swallowed exception — count reflects successes only.
    assert count == 2
    assert calls["n"] == 3
