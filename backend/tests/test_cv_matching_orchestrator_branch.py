"""Phase 10 integration: ``USE_CV_MATCH_V3`` routes through the new pipeline.

Exercises ``cv_score_orchestrator._execute_scoring`` via ``enqueue_score`` to
prove that:
- with the flag off, the legacy path runs (no call to run_cv_match)
- with the flag on, the new ``run_cv_match`` is invoked and its output is
  written to ``application.cv_match_details``
- a failed v3 run surfaces as ``CvScoreJob.status='error'``
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.cv_matching import PROMPT_VERSION, ScoringStatus
from app.cv_matching.schemas import (
    CVMatchOutput,
    Recommendation,
    RequirementAssessment,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _make_app_and_role(db_session) -> tuple:
    """Create a candidate + role + application directly in the test DB."""
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.organization import Organization
    from app.models.role import Role

    org = Organization(name="TestOrg")
    db_session.add(org)
    db_session.flush()

    candidate = Candidate(
        organization_id=org.id,
        email="cand@test.com",
        full_name="Test Candidate",
        cv_text="Senior engineer with AWS Glue experience and strong Python.",
    )
    db_session.add(candidate)
    db_session.flush()

    role = Role(
        organization_id=org.id,
        name="Senior Data Engineer",
        job_spec_text="Hiring AWS Glue engineer with strong Python skills.",
    )
    db_session.add(role)
    db_session.flush()

    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        cv_text="Senior engineer with AWS Glue experience and strong Python.",
    )
    db_session.add(application)
    db_session.commit()

    return application, role


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_flag_off_uses_legacy_path(db, monkeypatch):
    """With USE_CV_MATCH_V3=False, run_cv_match must not be called."""
    from app.platform.config import settings

    monkeypatch.setattr(settings, "USE_CV_MATCH_V3", False, raising=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True, raising=False)

    application, _ = _make_app_and_role(db)

    called = {"count": 0}

    def _spy(*args, **kwargs):
        called["count"] += 1
        raise RuntimeError("v3 runner should not be called when flag is off")

    monkeypatch.setattr("app.cv_matching.runner.run_cv_match", _spy)

    # Stub the legacy v4 path so the test doesn't try to hit Claude.
    legacy_called = {"count": 0}

    def _legacy_v4(**kwargs):
        legacy_called["count"] += 1
        return {
            "cv_job_match_score": 70.0,
            "match_details": {"summary": "legacy", "scoring_version": "cv_match_v4"},
        }

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator.calculate_cv_job_match_v4_sync",
        _legacy_v4,
    )

    def _legacy_v3(**kwargs):
        legacy_called["count"] += 1
        return {
            "cv_job_match_score": 70.0,
            "match_details": {"summary": "legacy v3", "scoring_version": "cv_fit_v3"},
        }

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator.calculate_cv_job_match_sync",
        _legacy_v3,
    )

    from app.services.cv_score_orchestrator import enqueue_score

    # Debug: confirm the gates pass
    assert application.id is not None
    assert (application.cv_text or "").strip()
    assert application.role is not None
    assert (application.role.job_spec_text or "").strip()
    assert settings.ANTHROPIC_API_KEY

    job = enqueue_score(db, application, force=True)
    assert job is not None
    assert called["count"] == 0
    assert legacy_called["count"] == 1


def test_flag_on_routes_to_v3_runner(db, monkeypatch):
    """With USE_CV_MATCH_V3=True, run_cv_match is called and result persisted."""
    from app.platform.config import settings

    monkeypatch.setattr(settings, "USE_CV_MATCH_V3", True, raising=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True, raising=False)

    application, _ = _make_app_and_role(db)

    fake_output = CVMatchOutput(
        prompt_version=PROMPT_VERSION,
        skills_match_score=88.0,
        experience_relevance_score=85.0,
        requirements_assessment=[],
        matching_skills=["AWS Glue", "Python"],
        missing_skills=[],
        experience_highlights=[],
        concerns=[],
        summary="V3 routed.",
        requirements_match_score=80.0,
        cv_fit_score=86.5,
        role_fit_score=82.6,
        recommendation=Recommendation.YES,
        scoring_status=ScoringStatus.OK,
        model_version="claude-haiku-4-5-20251001",
        trace_id="trace-v3-routed",
    )

    calls: list[Any] = []

    def _fake_run_cv_match(cv_text, jd_text, requirements, **kwargs):
        calls.append(
            {
                "cv_text_len": len(cv_text),
                "jd_text_len": len(jd_text),
                "requirements_count": len(requirements),
            }
        )
        return fake_output

    monkeypatch.setattr(
        "app.cv_matching.runner.run_cv_match",
        _fake_run_cv_match,
    )

    from app.services.cv_score_orchestrator import enqueue_score

    job = enqueue_score(db, application, force=True)

    assert len(calls) == 1, "run_cv_match was not called exactly once"
    assert calls[0]["cv_text_len"] > 0
    assert calls[0]["jd_text_len"] > 0
    assert job.status == "done"
    assert job.prompt_version == PROMPT_VERSION
    assert application.cv_match_score == 82.6
    assert application.cv_match_details is not None
    assert application.cv_match_details["summary"] == "V3 routed."
    assert application.cv_match_details["recommendation"] == "yes"
    assert application.cv_match_details["trace_id"] == "trace-v3-routed"


def test_flag_on_failed_v3_marks_job_error(db, monkeypatch):
    from app.platform.config import settings

    monkeypatch.setattr(settings, "USE_CV_MATCH_V3", True, raising=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True, raising=False)

    application, _ = _make_app_and_role(db)

    failed = CVMatchOutput(
        prompt_version=PROMPT_VERSION,
        skills_match_score=0.0,
        experience_relevance_score=0.0,
        requirements_assessment=[],
        matching_skills=[],
        missing_skills=[],
        experience_highlights=[],
        concerns=[],
        summary="",
        requirements_match_score=0.0,
        cv_fit_score=0.0,
        role_fit_score=0.0,
        recommendation=Recommendation.NO,
        scoring_status=ScoringStatus.FAILED,
        error_reason="claude_call_failed: 429 too many requests",
        model_version="claude-haiku-4-5-20251001",
        trace_id="trace-failed",
    )

    monkeypatch.setattr(
        "app.cv_matching.runner.run_cv_match",
        lambda *a, **kw: failed,
    )

    from app.services.cv_score_orchestrator import enqueue_score

    job = enqueue_score(db, application, force=True)

    assert job.status == "error"
    assert "v3_failed" in (job.error_message or "")
    assert application.cv_match_score is None
    assert "error" in (application.cv_match_details or {})
    assert application.cv_match_details["scoring_version"] == PROMPT_VERSION


def test_recruiter_must_haves_pass_disqualifying_flag(db, monkeypatch):
    """Recruiter must-have criteria must reach run_cv_match with the
    `crit_recruiter_` id prefix and `disqualifying_if_missing=True`. This
    is the upstream half of the D1+D3 fix: the orchestrator tags
    recruiter-source criteria so aggregation can both apply the floor cap
    and the 1.5× weight multiplier downstream.
    """
    from app.cv_matching.schemas import Priority
    from app.models.role_criterion import (
        CRITERION_SOURCE_DERIVED,
        CRITERION_SOURCE_RECRUITER,
        RoleCriterion,
    )
    from app.platform.config import settings

    monkeypatch.setattr(settings, "USE_CV_MATCH_V3", True, raising=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True, raising=False)

    application, role = _make_app_and_role(db)

    # Recruiter-added must-have AND a derived (LLM-extracted) must-have.
    db.add(
        RoleCriterion(
            role_id=role.id,
            source=CRITERION_SOURCE_RECRUITER,
            ordering=1,
            weight=1.0,
            must_have=True,
            text="5+ years AWS Glue in production",
        )
    )
    db.add(
        RoleCriterion(
            role_id=role.id,
            source=CRITERION_SOURCE_DERIVED,
            ordering=2,
            weight=1.0,
            must_have=True,
            text="Strong Python skills",
        )
    )
    db.commit()
    db.refresh(role)

    captured: dict[str, Any] = {}

    def _capture(cv_text, jd_text, requirements, **kwargs):
        captured["requirements"] = list(requirements or [])
        return CVMatchOutput(
            prompt_version=PROMPT_VERSION,
            skills_match_score=80.0,
            experience_relevance_score=80.0,
            requirements_assessment=[],
            summary="ok",
            requirements_match_score=80.0,
            cv_fit_score=80.0,
            role_fit_score=80.0,
            recommendation=Recommendation.YES,
            scoring_status=ScoringStatus.OK,
            model_version="claude-haiku-4-5-20251001",
            trace_id="trace-1",
        )

    monkeypatch.setattr("app.cv_matching.runner.run_cv_match", _capture)

    from app.services.cv_score_orchestrator import enqueue_score

    enqueue_score(db, application, force=True)

    reqs = captured["requirements"]
    assert len(reqs) == 2
    by_id = {r.id: r for r in reqs}
    assert any(rid.startswith("crit_recruiter_") for rid in by_id)
    assert any(rid.startswith("crit_derived_") for rid in by_id)
    recruiter_req = next(r for r in reqs if r.id.startswith("crit_recruiter_"))
    derived_req = next(r for r in reqs if r.id.startswith("crit_derived_"))
    assert recruiter_req.priority == Priority.MUST_HAVE
    assert recruiter_req.disqualifying_if_missing is True
    # Derived must-haves are NOT marked disqualifying — only the
    # recruiter's specific intent should trigger the must-have floor.
    assert derived_req.disqualifying_if_missing is False
