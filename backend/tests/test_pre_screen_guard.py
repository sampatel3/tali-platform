"""Pre-screen integrity guard on bypass / bulk-re-score paths.

Stops ``enqueue_score(bypass_pre_screen=True)`` from blind-paying for the
expensive holistic score on candidates that never genuinely passed pre-screen
(never-screened, stale, or below threshold), and stops the agent-chat bulk
re-score from touching closed/archived Workable reqs. (2026-06 cost audit.)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def _seed_app(db):
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", job_spec_text="hire")
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual", cv_text="cv content here",
    )
    db.add(app); db.flush()
    return org, role, app


def _enqueue_capture(db, app, monkeypatch, *, guard, genuine, run_at, cv_uploaded=None):
    """Run enqueue_score(bypass_pre_screen=True) with the heavy gates stubbed,
    and return the kwargs passed to score_application_job.delay."""
    from app.platform.config import settings
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test", raising=False)
    monkeypatch.setattr(settings, "PRE_SCREEN_GATE_GUARD_RESCORE", guard, raising=False)
    monkeypatch.setattr(settings, "PRE_SCREEN_THRESHOLD", 50, raising=False)

    app.genuine_pre_screen_score_100 = genuine
    app.pre_screen_run_at = run_at
    app.cv_uploaded_at = cv_uploaded
    db.flush()

    captured: dict = {}
    fake = MagicMock()
    def _delay(*a, **k):
        captured.update(k)
        return SimpleNamespace(id="task-1")
    fake.delay.side_effect = _delay
    monkeypatch.setattr("app.tasks.scoring_tasks.score_application_job", fake)
    monkeypatch.setattr("app.services.role_budget_gate.can_spend_on_role", lambda *a, **k: True)
    monkeypatch.setattr("app.services.cv_score_orchestrator._meter_reserve", lambda *a, **k: None)

    from app.services.cv_score_orchestrator import enqueue_score
    enqueue_score(db, app, force=True, bypass_pre_screen=True)
    return captured


def test_guard_downgrades_bypass_for_never_prescreened(db, monkeypatch):
    _, _, app = _seed_app(db)
    cap = _enqueue_capture(db, app, monkeypatch, guard=True, genuine=None, run_at=None)
    assert cap.get("force_full_score") is False  # routed through the gate → pre-screen runs


def test_guard_downgrades_bypass_for_failed_prescreen(db, monkeypatch):
    _, _, app = _seed_app(db)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    cap = _enqueue_capture(db, app, monkeypatch, guard=True, genuine=40.0, run_at=past)
    assert cap.get("force_full_score") is False  # below threshold → gate will filter


def test_guard_honors_bypass_for_genuinely_passed(db, monkeypatch):
    _, _, app = _seed_app(db)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    cap = _enqueue_capture(db, app, monkeypatch, guard=True, genuine=80.0, run_at=past)
    assert cap.get("force_full_score") is True  # passed pre-screen → bypass is safe


def test_flag_off_keeps_legacy_bypass(db, monkeypatch):
    _, _, app = _seed_app(db)
    cap = _enqueue_capture(db, app, monkeypatch, guard=False, genuine=None, run_at=None)
    assert cap.get("force_full_score") is True  # guard disabled → unchanged behavior


def test_bulk_rescore_skips_closed_archived_role(monkeypatch):
    from app.agent_chat import rescore
    monkeypatch.setattr(rescore, "workable_job_syncable", lambda role: False)
    calls = {"stale": 0}
    monkeypatch.setattr(rescore, "find_stale_scored",
                        lambda db, role: calls.__setitem__("stale", calls["stale"] + 1) or [])
    out = rescore.rescore_candidates(MagicMock(), SimpleNamespace(id=1), scope="all", confirm=True)
    assert "closed/archived" in out["message"].lower()
    assert calls["stale"] == 0  # short-circuits before computing/spending


def test_bulk_rescore_proceeds_for_live_role(monkeypatch):
    from app.agent_chat import rescore
    monkeypatch.setattr(rescore, "workable_job_syncable", lambda role: True)
    monkeypatch.setattr(rescore, "find_stale_scored", lambda db, role: [])
    out = rescore.rescore_candidates(MagicMock(), SimpleNamespace(id=1), scope="all", confirm=True)
    assert "nothing to re-score" in out["message"].lower()  # live role → normal path
