"""CV-scoring sub-agent: cache-fast-path + runner invocation."""

from __future__ import annotations

from unittest.mock import patch

from app.sub_agents.base import SubAgentRequest
from app.sub_agents.cv_scoring import CV_SCORING_SUB_AGENT

from .conftest import make_full_application


def test_cached_match_details_returned_without_claude_call(db):
    cached = {
        "role_fit_score": 73.0,
        "dimension_scores": {"skills": 80.0, "experience": 70.0},
        "requirements_assessment": [{"requirement": "python", "met": True}],
        "calibrated_p_advance": 0.62,
        "summary": "Strong python background",
    }
    org, role, _, app = make_full_application(db, cv_match_details=cached)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    with patch("app.sub_agents.cv_scoring.run_cv_match") as runner:
        result = CV_SCORING_SUB_AGENT.run(req, db=db)
    runner.assert_not_called()
    assert result.ok is True
    assert result.output["role_fit_score"] == 73.0
    assert result.output["calibrated_p_advance"] == 0.62


def test_missing_cv_text_returns_error(db):
    org, role, _, app = make_full_application(db, cv_text="")
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    result = CV_SCORING_SUB_AGENT.run(req, db=db)
    assert result.ok is False
    assert "cv_text" in (result.error or "")


def test_skip_cache_invokes_runner(db):
    cached = {
        "role_fit_score": 50.0,
        "dimension_scores": {},
        "requirements_assessment": [],
    }
    org, role, _, app = make_full_application(db, cv_match_details=cached)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        skip_cache=True,
    )

    from app.cv_matching.schemas import CVMatchOutput, ScoringStatus

    fake = CVMatchOutput(
        prompt_version="v3.0",
        role_fit_score=88.0,
        scoring_status=ScoringStatus.OK,
        cache_hit=False,
        input_tokens=300,
        output_tokens=100,
    )
    with patch("app.sub_agents.cv_scoring.run_cv_match", return_value=fake) as runner:
        result = CV_SCORING_SUB_AGENT.run(req, db=db)
    runner.assert_called_once()
    assert result.ok is True
    assert result.output["role_fit_score"] == 88.0
    assert result.tokens_used == 400
