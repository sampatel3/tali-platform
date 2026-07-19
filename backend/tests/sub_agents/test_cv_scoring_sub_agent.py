"""CV-scoring sub-agent: cache-fast-path + runner invocation."""

from __future__ import annotations

from unittest.mock import patch

from app.sub_agents.base import SubAgentRequest
from app.sub_agents.cv_scoring import CV_SCORING_SUB_AGENT
from app.models.role_criterion import RoleCriterion

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


def test_runner_receives_canonical_constraint_requirements(db):
    from app.cv_matching.schemas import CVMatchOutput, Priority, ScoringStatus

    org, role, _, app = make_full_application(db)
    db.add(
        RoleCriterion(
            role_id=int(role.id),
            source="recruiter",
            ordering=0,
            weight=1.0,
            must_have=False,
            bucket="constraint",
            text="Must already have UAE work authorization",
        )
    )
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        skip_cache=True,
    )
    fake = CVMatchOutput(
        prompt_version="v3.0",
        role_fit_score=80.0,
        scoring_status=ScoringStatus.OK,
    )

    with patch("app.sub_agents.cv_scoring.run_cv_match", return_value=fake) as runner:
        result = CV_SCORING_SUB_AGENT.run(req, db=db)

    assert result.ok is True
    requirements = runner.call_args.args[2]
    assert len(requirements) == 1
    assert requirements[0].priority == Priority.CONSTRAINT


def test_runner_failure_does_not_return_internal_reason(db):
    from app.cv_matching.schemas import CVMatchOutput, ScoringStatus

    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        skip_cache=True,
    )
    secret = "sdk-token=private-value"
    failed = CVMatchOutput(
        prompt_version="v3.0",
        scoring_status=ScoringStatus.FAILED,
        error_reason=f"client_init_failed: {secret}",
    )
    with patch("app.sub_agents.cv_scoring.run_cv_match", return_value=failed):
        result = CV_SCORING_SUB_AGENT.run(req, db=db)
    assert result.error == "scoring_provider_unavailable"
    assert secret not in str(result)
