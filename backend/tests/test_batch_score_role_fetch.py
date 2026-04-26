"""Tests for the Celery batch_score_role task: CV fetch + score fan-out.

The bug we're guarding against: ``batch_score_role`` was dropping every
application that didn't already have ``cv_text`` populated, because
``enqueue_score`` returns None for those. In production this meant 599 of
600 candidates got silently skipped after a "Re-score all" click.

These tests exercise the task body with three application states:
- pre-populated cv_text (should enqueue immediately)
- candidate-level cv_text only (should be promoted to application)
- Workable-source missing cv_text (should be fetched via the helper)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Candidate,
    CandidateApplication,
    CvScoreJob,
    Organization,
    Role,
)
from app.platform.config import settings
from app.platform.database import Base
from app.tasks.scoring_tasks import batch_score_role


@pytest.fixture(autouse=True)
def _force_inline_celery(monkeypatch):
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")


@pytest.fixture()
def session_factory(monkeypatch):
    """Create an in-memory SQLite + bind both SessionLocal references to it."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.tasks.scoring_tasks.SessionLocal", Session, raising=False)
    monkeypatch.setattr("app.platform.database.SessionLocal", Session, raising=False)
    return Session


def _seed_role_with_apps(Session) -> tuple[int, list[int]]:
    """Create one org/role and three applications in three CV states."""
    db = Session()
    try:
        org = Organization(
            name="Acme",
            slug="acme",
            workable_subdomain="acme",
            workable_access_token="t",
            workable_connected=True,
        )
        db.add(org)
        db.flush()

        role = Role(
            organization_id=org.id,
            name="Engineer",
            job_spec_text="Backend engineer\nRequirements:\n- Python\n",
        )
        db.add(role)
        db.flush()

        # App 1: already has cv_text on the application row
        cand1 = Candidate(organization_id=org.id, email="a@x.com", full_name="A")
        db.add(cand1)
        db.flush()
        app1 = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand1.id,
            role_id=role.id,
            status="applied",
            cv_text="Senior engineer with 7 years Python.",
        )
        db.add(app1)

        # App 2: cv_text only on the candidate row (should be promoted)
        cand2 = Candidate(
            organization_id=org.id,
            email="b@x.com",
            full_name="B",
            cv_text="Backend engineer skilled in Python and AWS.",
            cv_filename="b.pdf",
        )
        db.add(cand2)
        db.flush()
        app2 = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand2.id,
            role_id=role.id,
            status="applied",
        )
        db.add(app2)

        # App 3: Workable-source, no CV anywhere — needs Workable fetch
        cand3 = Candidate(
            organization_id=org.id,
            email="c@x.com",
            full_name="C",
            workable_candidate_id="wk-c",
        )
        db.add(cand3)
        db.flush()
        app3 = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand3.id,
            role_id=role.id,
            status="applied",
            source="workable",
            workable_candidate_id="wk-c",
        )
        db.add(app3)

        db.commit()
        return role.id, [app1.id, app2.id, app3.id]
    finally:
        db.close()


def test_batch_score_role_fetches_missing_cvs(session_factory, monkeypatch):
    """All 3 applications should end up with cv_text set + a CvScoreJob created.

    The bug pre-fix: only app1 (which already had cv_text) would be
    counted; apps 2 and 3 silently dropped. After the fix all 3 are
    handled — app2 via candidate promotion, app3 via the Workable fetch
    helper.
    """
    role_id, app_ids = _seed_role_with_apps(session_factory)

    # Stub the Workable fetcher: pretend we successfully fetched and set
    # cv_text on the application row.
    def _fake_workable_fetch(app, candidate, db, org):
        app.cv_text = "Workable-fetched CV text for " + (candidate.full_name or "")
        app.cv_filename = f"{candidate.id}.pdf"
        return True

    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        _fake_workable_fetch,
        raising=False,
    )

    # Stub the per-app score dispatcher so we don't try to call Anthropic.
    # `enqueue_score` runs the inline path because MVP_DISABLE_CELERY=True;
    # _execute_scoring would call the legacy v3 / v4 flow. Stub _execute_scoring
    # to a no-op success.
    def _fake_execute_scoring(db, *, application, job):
        application.cv_match_score = 75.0
        application.cv_match_details = {"summary": "stub"}
        job.status = "done"
        job.cache_hit = "miss"

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        _fake_execute_scoring,
    )

    result = batch_score_role(role_id, include_scored=True)

    # All three applications enqueued and scored end-to-end.
    assert result["status"] == "enqueued"
    assert result["count"] == 3, result
    # 2 fetches happened: candidate-level promotion + Workable fetch
    assert result["fetched"] == 2, result
    assert result["fetch_failures"] == 0, result

    # Verify state in DB
    db = session_factory()
    try:
        apps = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id.in_(app_ids))
            .all()
        )
        for app in apps:
            assert (app.cv_text or "").strip(), f"app {app.id} still missing cv_text"
            assert app.cv_match_score == 75.0
        # 3 done jobs in the audit log
        jobs = (
            db.query(CvScoreJob)
            .filter(CvScoreJob.application_id.in_(app_ids))
            .all()
        )
        assert len(jobs) == 3
        assert all(j.status == "done" for j in jobs)
    finally:
        db.close()


def test_batch_score_role_skips_when_cv_unfetchable(session_factory, monkeypatch):
    """If Workable fetch fails, the count drops to fetched applications only.

    The application stays without cv_text; enqueue_score returns None
    and the count does NOT include it. This is correct behavior — but
    it's also why the production count was 1: most apps were in this
    state and the helper wasn't even being called.
    """
    role_id, app_ids = _seed_role_with_apps(session_factory)

    # Workable fetch always fails
    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        lambda app, candidate, db, org: False,
        raising=False,
    )

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        lambda db, *, application, job: setattr(application, "cv_match_score", 50.0)
        or setattr(job, "status", "done"),
    )

    result = batch_score_role(role_id, include_scored=True)
    # Only app1 (pre-existing cv_text) and app2 (candidate-level promotion) succeed
    assert result["status"] == "enqueued"
    assert result["count"] == 2, result
    assert result["fetched"] == 1, result  # candidate-level promotion only
    assert result["fetch_failures"] == 1, result  # app3 Workable fetch failed
