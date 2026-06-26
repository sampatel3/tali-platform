"""Talent-pool rediscovery Phase B — the opt-in re-score (task + endpoints).

Covers the cost rails + the core safety property: a re-score against an ad-hoc
requirement scores via the holistic engine, role-less + metered, and stores the
result on the job WITHOUT touching the canonical ``cv_match_details``.

Mock-backed — no real Anthropic calls.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_seq = itertools.count(1)  # unique slugs/emails (id(object()) reuses addresses)

from app.domains.assessments_runtime import pool_rescore_routes as routes
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.pool_rescore_job import PoolRescoreJob
from app.models.role import Role
from app.models.user import User
from app.tasks import pool_rescore_tasks


def _org_user(db):
    n = next(_seq)
    org = Organization(name="Org", slug=f"o-{n}")
    db.add(org)
    db.flush()
    user = User(
        email=f"u-{n}@x.test", hashed_password="x", full_name="U",
        organization_id=org.id, is_active=True, is_verified=True, is_superuser=False,
    )
    db.add(user)
    db.commit()
    return org, user


def _app(db, org, *, cv="CV text", details=None):
    cand = Candidate(
        organization_id=org.id, email=f"c-{next(_seq)}@x.test",
        full_name="Cand", position="Eng", cv_text=cv,
    )
    db.add(cand)
    db.flush()
    role = Role(organization_id=org.id, name="R", source="manual")
    db.add(role)
    db.flush()
    a = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", application_outcome="open",
        source="manual", cv_text=cv,
        cv_match_details=details if details is not None else {"role_fit_score": 30},
    )
    db.add(a)
    db.commit()
    return a


def test_start_enforces_count_cap(db):
    _org, user = _org_user(db)
    with pytest.raises(Exception) as ei:  # HTTPException 400
        routes.start_pool_rescore(
            payload={
                "requirement_text": "python",
                "application_ids": list(range(routes.MAX_POOL_RESCORE + 1)),
            },
            db=db, current_user=user,
        )
    assert "cap" in str(ei.value).lower() or "too many" in str(ei.value).lower()


def test_start_creates_job_and_dispatches(db, monkeypatch):
    org, user = _org_user(db)
    a = _app(db, org)
    fake_task = MagicMock()
    monkeypatch.setattr(pool_rescore_tasks, "rescore_pool_against_requirement", fake_task)

    out = routes.start_pool_rescore(
        payload={"requirement_text": "banking domain", "application_ids": [a.id]},
        db=db, current_user=user,
    )
    assert out["count"] == 1
    assert out["estimated_cost_usd"] == round(routes.COST_PER_RESCORE_USD, 2)
    assert out["status"] == "pending"
    fake_task.delay.assert_called_once()
    job = db.query(PoolRescoreJob).filter_by(id=out["job_id"]).first()
    assert job is not None and job.application_ids == [a.id]


def test_poll_is_org_scoped(db):
    org, user = _org_user(db)
    _other_org, other_user = _org_user(db)
    job = PoolRescoreJob(
        organization_id=org.id, requirement_text="x", requirement_hash="h",
        application_ids=[], status="pending",
    )
    db.add(job)
    db.commit()
    assert routes.get_pool_rescore(job.id, db=db, current_user=user)["job_id"] == job.id
    with pytest.raises(Exception) as ei:
        routes.get_pool_rescore(job.id, db=db, current_user=other_user)
    assert "not found" in str(ei.value).lower()


def test_task_scores_and_never_touches_cv_match_details(db, monkeypatch):
    from tests.conftest import TestingSessionLocal

    org, _user = _org_user(db)
    a = _app(db, org, cv="led the core banking migration", details={"role_fit_score": 30})
    original_details = dict(a.cv_match_details)
    job = PoolRescoreJob(
        organization_id=org.id, requirement_text="banking domain",
        requirement_hash="h", application_ids=[a.id], status="pending",
    )
    db.add(job)
    db.commit()
    job_id = job.id

    captured = {}

    def _fake_holistic(cv_text, job_spec_text, *, client, metering_context=None, workable_context=None):
        captured["jd"] = job_spec_text
        captured["mc"] = metering_context
        return SimpleNamespace(
            role_fit_score=82.0, summary="Strong fit.", scoring_status="ok", cache_hit=False,
        )

    monkeypatch.setattr("app.cv_matching.holistic.run_holistic_match", _fake_holistic)
    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_metered_client", lambda **kw: object()
    )
    monkeypatch.setattr(
        "app.services.workable_context_service.format_workable_context",
        lambda c, a: None, raising=False,
    )
    monkeypatch.setattr("app.platform.database.SessionLocal", TestingSessionLocal)

    res = pool_rescore_tasks.rescore_pool_against_requirement(job_id)
    assert res["ok"] is True and res["scored"] == 1

    db.expire_all()
    job2 = db.query(PoolRescoreJob).filter_by(id=job_id).first()
    assert job2.status == "done"
    assert job2.counts["scored"] == 1 and job2.counts["failed"] == 0
    assert job2.results[0]["application_id"] == a.id
    assert job2.results[0]["role_fit_score"] == 82.0
    # scored against the NEW requirement, role-less, metered on the application
    assert captured["jd"] == "banking domain"
    assert captured["mc"]["role_id"] is None
    assert captured["mc"]["entity_id"] == f"application:{a.id}"
    # the canonical role score is UNTOUCHED
    a2 = db.query(CandidateApplication).filter_by(id=a.id).first()
    assert a2.cv_match_details == original_details


def test_task_degrades_failed_app_without_killing_job(db, monkeypatch):
    from tests.conftest import TestingSessionLocal

    org, _user = _org_user(db)
    a = _app(db, org)
    job = PoolRescoreJob(
        organization_id=org.id, requirement_text="kafka", requirement_hash="h",
        application_ids=[a.id], status="pending",
    )
    db.add(job)
    db.commit()
    job_id = job.id

    def _boom(*args, **kwargs):
        raise RuntimeError("anthropic down")

    monkeypatch.setattr("app.cv_matching.holistic.run_holistic_match", _boom)
    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_metered_client", lambda **kw: object()
    )
    monkeypatch.setattr(
        "app.services.workable_context_service.format_workable_context",
        lambda c, a: None, raising=False,
    )
    monkeypatch.setattr("app.platform.database.SessionLocal", TestingSessionLocal)

    res = pool_rescore_tasks.rescore_pool_against_requirement(job_id)
    assert res["ok"] is True and res["failed"] == 1

    db.expire_all()
    job2 = db.query(PoolRescoreJob).filter_by(id=job_id).first()
    assert job2.status == "done"  # job completes; the bad app is just a failed row
    assert job2.results[0]["scoring_status"] == "failed"
