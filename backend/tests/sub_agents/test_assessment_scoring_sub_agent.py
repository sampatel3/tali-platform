"""Assessment-scoring sub-agent: read-side wrapper of cached scores."""

from __future__ import annotations

from unittest.mock import patch

from app.sub_agents.assessment_scoring import ASSESSMENT_SCORING_SUB_AGENT
from app.sub_agents.base import SubAgentRequest

from .conftest import make_full_application


def test_returns_cached_scores(db):
    org, role, _, app = make_full_application(
        db, taali_score=78.0, assessment_score=72.0
    )
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    result = ASSESSMENT_SCORING_SUB_AGENT.run(req, db=db)
    assert result.ok is True
    assert result.output["taali_score"] == 78.0
    assert result.output["assessment_score"] == 72.0
    assert result.output["assessment_completed"] is True


def test_no_assessment_returns_zero_confidence(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    result = ASSESSMENT_SCORING_SUB_AGENT.run(req, db=db)
    assert result.ok is True
    assert result.confidence == 0.0
    assert result.output["assessment_completed"] is False


def test_unexpected_failure_returns_stable_code(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    secret = "sdk-token=private-value"
    with patch.object(
        ASSESSMENT_SCORING_SUB_AGENT,
        "_run",
        side_effect=RuntimeError(secret),
    ):
        result = ASSESSMENT_SCORING_SUB_AGENT.run(req, db=db)
    assert result.error == "assessment_scoring_failed"
    assert secret not in str(result)
