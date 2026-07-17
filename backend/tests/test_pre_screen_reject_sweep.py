"""Pre-screen reject catch-up: the deterministic sweep must cull
already-pre-screened, below-threshold candidates even when the role is
budget-paused, and the scoring task must fire the reject at the pre-screen
short-circuit ("reject first, before CV-match scoring").
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


_N = [0]


def _seed(db, *, pre_score=18.0, recommendation="Below threshold", cv_score=None,
          outcome="open", agentic=True, paused=True,
          pre_screen_run_at=datetime(2026, 1, 1, tzinfo=timezone.utc)):
    _N[0] += 1
    org = Organization(name="O", slug=f"o-sweep-{_N[0]}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="R", source="workable",
        auto_reject_pre_screen=True,
        agentic_mode_enabled=agentic,
        score_threshold=50,
    )
    if paused:
        role.agent_paused_at = datetime.now(timezone.utc)
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email=f"c{_N[0]}@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="recruiter",
        application_outcome=outcome, source="workable",
        pre_screen_score_100=pre_score, pre_screen_recommendation=recommendation,
        cv_match_score=cv_score, pre_screen_run_at=pre_screen_run_at,
    )
    db.add(app); db.flush()
    return org, role, app


def test_sweep_dispatches_for_paused_role(db, monkeypatch):
    """A below-threshold, open, pre-screen-only candidate on a PAUSED role
    must be picked up by the sweep and dispatched to auto-reject."""
    from app.tasks import agent_tasks

    sent: list[int] = []
    import app.tasks.automation_tasks as auto
    monkeypatch.setattr(
        auto.run_application_auto_reject, "delay",
        lambda app_id: sent.append(int(app_id)),
    )
    _, _, app = _seed(db, paused=True)
    db.commit()

    result = agent_tasks.pre_screen_reject_sweep.run()

    assert result["status"] == "ok"
    assert app.id in sent, "paused-role below-threshold candidate was not swept"


def test_sweep_skips_fully_scored_and_non_open(db, monkeypatch):
    from app.tasks import agent_tasks

    sent: list[int] = []
    import app.tasks.automation_tasks as auto
    monkeypatch.setattr(
        auto.run_application_auto_reject, "delay",
        lambda app_id: sent.append(int(app_id)),
    )
    # Fully scored → agent owns it, not the pre-screen sweep.
    _, _, scored = _seed(db, cv_score=18.0)
    # Already rejected → not open.
    _, _, closed = _seed(db, outcome="rejected")
    db.commit()

    agent_tasks.pre_screen_reject_sweep.run()

    assert scored.id not in sent
    assert closed.id not in sent


def test_sweep_now_includes_agent_off_roles(db, monkeypatch):
    """A pre-screen reject is deterministic policy, so the sweep surfaces
    below-threshold candidates even when the role's agent is OFF."""
    from app.tasks import agent_tasks

    sent: list[int] = []
    import app.tasks.automation_tasks as auto
    monkeypatch.setattr(
        auto.run_application_auto_reject, "delay",
        lambda app_id: sent.append(int(app_id)),
    )
    _, _, agentless = _seed(db, agentic=False)
    db.commit()

    agent_tasks.pre_screen_reject_sweep.run()

    assert agentless.id in sent  # surfaced even with the agent off


def test_scoring_task_fires_reject_on_prescreen_filter(db, monkeypatch):
    """Fix A: when the pre-screen gate short-circuits a candidate (cache_hit
    'pre_screen_filtered'/'fraud_filtered'), score_application_job must
    dispatch run_application_auto_reject so the reject goes FIRST, before any
    later sweep — not only at the cohort tick."""
    from app.models.cv_score_job import CvScoreJob, SCORE_JOB_DONE
    from app.tasks import scoring_tasks
    import app.services.cv_score_orchestrator as orch
    import app.domains.assessments_runtime.applications_routes as routes
    import app.tasks.automation_tasks as auto

    _, _, app = _seed(db, paused=False)
    job = CvScoreJob(application_id=app.id, role_id=app.role_id, status="pending")
    db.add(job); db.flush(); db.commit()

    def fake_execute(db_, *, application, job, force_full_score=False):
        # Simulate the orchestrator pre-screen short-circuit.
        job.cache_hit = "pre_screen_filtered"
        job.status = SCORE_JOB_DONE

    monkeypatch.setattr(orch, "_execute_scoring", fake_execute)
    monkeypatch.setattr(routes, "is_batch_score_cancelled", lambda _rid: False)
    sent: list[int] = []
    monkeypatch.setattr(auto.run_application_auto_reject, "delay",
                        lambda app_id: sent.append(int(app_id)))

    scoring_tasks.score_application_job.run(app.id, job_id=int(job.id))

    assert app.id in sent, "pre-screen filter did not trigger the reject"


def test_scoring_task_no_reject_on_normal_score(db, monkeypatch):
    """A full CV-match score (no pre-screen filter) must NOT trigger the
    pre-screen reject dispatch — the agent owns scored candidates."""
    from app.models.cv_score_job import CvScoreJob, SCORE_JOB_DONE
    from app.tasks import scoring_tasks
    import app.services.cv_score_orchestrator as orch
    import app.domains.assessments_runtime.applications_routes as routes
    import app.tasks.automation_tasks as auto

    _, _, app = _seed(db, paused=False)
    job = CvScoreJob(application_id=app.id, role_id=app.role_id, status="pending")
    db.add(job); db.flush(); db.commit()

    def fake_execute(db_, *, application, job, force_full_score=False):
        application.cv_match_score = 72.0
        job.cache_hit = "miss"
        job.status = SCORE_JOB_DONE

    monkeypatch.setattr(orch, "_execute_scoring", fake_execute)
    monkeypatch.setattr(routes, "is_batch_score_cancelled", lambda _rid: False)
    sent: list[int] = []
    monkeypatch.setattr(auto.run_application_auto_reject, "delay",
                        lambda app_id: sent.append(int(app_id)))

    scoring_tasks.score_application_job.run(app.id, job_id=int(job.id))

    assert app.id not in sent


def test_sweep_skips_apps_with_pending_decision(db, monkeypatch):
    """If a reject card is already pending, don't re-dispatch."""
    from app.models.agent_decision import AgentDecision
    from app.tasks import agent_tasks

    sent: list[int] = []
    import app.tasks.automation_tasks as auto
    monkeypatch.setattr(
        auto.run_application_auto_reject, "delay",
        lambda app_id: sent.append(int(app_id)),
    )
    org, role, app = _seed(db)
    db.add(AgentDecision(
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        agent_run_id=None, decision_type="skip_assessment_reject",
        recommendation="skip_assessment_reject", status="pending", reasoning="x",
        evidence={}, confidence=None, model_version="pre_screen_v1",
        prompt_version="pre_screen_threshold.v1",
        idempotency_key=f"pre_screen_reject:{int(app.id)}",
        active_capabilities={}, token_spend={},
    ))
    db.commit()

    agent_tasks.pre_screen_reject_sweep.run()

    assert app.id not in sent


def test_sweep_skips_never_pre_screened(db, monkeypatch):
    """A stale 'Below threshold' label with no genuine pre-screen run (no
    ``pre_screen_run_at``) must NOT be swept into a reject — that label can be
    stamped by a cv_match snapshot refresh with no pre-screen ever run."""
    from app.tasks import agent_tasks

    sent: list[int] = []
    import app.tasks.automation_tasks as auto
    monkeypatch.setattr(
        auto.run_application_auto_reject, "delay",
        lambda app_id: sent.append(int(app_id)),
    )
    _, _, app = _seed(db, pre_screen_run_at=None)
    db.commit()

    agent_tasks.pre_screen_reject_sweep.run()

    assert app.id not in sent
