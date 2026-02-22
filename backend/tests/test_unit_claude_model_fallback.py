from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from app.components.integrations.claude.model_fallback import (
    LEGACY_HAIKU_MODEL,
    PRIMARY_HAIKU_MODEL,
    SNAPSHOT_HAIKU_MODEL,
    candidate_models_for,
    is_model_not_found_error,
)
from app.services.fit_matching_service import calculate_cv_job_match_sync


def test_candidate_models_for_known_haiku_aliases():
    assert candidate_models_for(PRIMARY_HAIKU_MODEL) == [
        PRIMARY_HAIKU_MODEL,
        SNAPSHOT_HAIKU_MODEL,
        LEGACY_HAIKU_MODEL,
    ]
    assert candidate_models_for(SNAPSHOT_HAIKU_MODEL) == [
        SNAPSHOT_HAIKU_MODEL,
        PRIMARY_HAIKU_MODEL,
        LEGACY_HAIKU_MODEL,
    ]


def test_is_model_not_found_error_matches_provider_payloads():
    err = Exception("Error code: 404 - {'type':'error','error':{'type':'not_found_error','message':'model: claude-3-5-haiku-latest'}}")
    assert is_model_not_found_error(err) is True
    assert is_model_not_found_error(Exception("timeout while contacting provider")) is False


def test_fit_matching_retries_when_primary_haiku_alias_is_unavailable(monkeypatch):
    calls: list[str] = []

    class FakeMessages:
        def create(self, *, model, max_tokens, system, messages):
            calls.append(model)
            if model == PRIMARY_HAIKU_MODEL:
                raise Exception(
                    "Error code: 404 - {'type':'error','error':{'type':'not_found_error','message':'model: claude-3-5-haiku-latest'}}"
                )
            payload = {
                "overall_match_score": 82,
                "skills_match_score": 76,
                "experience_relevance_score": 79,
                "matching_skills": ["python"],
                "missing_skills": ["spark"],
                "experience_highlights": ["backend delivery"],
                "concerns": ["limited domain evidence"],
                "summary": "Strong enough fit.",
            }
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(payload))],
                usage=SimpleNamespace(input_tokens=120, output_tokens=80),
            )

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))

    result = calculate_cv_job_match_sync(
        cv_text="Python backend experience",
        job_spec_text="Need Python and Spark",
        api_key="test-key",
        model=PRIMARY_HAIKU_MODEL,
    )

    assert calls[0] == PRIMARY_HAIKU_MODEL
    assert calls[1] == SNAPSHOT_HAIKU_MODEL
    assert result["cv_job_match_score"] == 73.6
    assert result["cv_job_match_score"] % 10 != 0
    assert result["match_details"]["skills_match_score_100"] == 68.7
    assert result["match_details"]["experience_relevance_score_100"] == 72.2
    assert len(result["match_details"]["score_rationale_bullets"]) >= 2
    assert result["match_details"]["_claude_usage"]["model"] == SNAPSHOT_HAIKU_MODEL


def test_fit_matching_enriches_requirement_evidence_when_model_output_is_sparse(monkeypatch):
    class FakeMessages:
        def create(self, *, model, max_tokens, system, messages):
            payload = {
                "overall_match_score": 79,
                "skills_match_score": 77,
                "experience_relevance_score": 82,
                "requirements_match_score": 74,
                "requirements_assessment": [
                    {
                        "requirement": "Production experience with enterprise systems",
                        "priority": "must_have",
                        "status": "met",
                        "evidence": "",
                        "impact": "",
                    },
                    {
                        "requirement": "Salary 25k-35k AED target",
                        "priority": "constraint",
                        "status": "partially_met",
                        "evidence": "Good fit overall.",
                        "impact": "",
                    },
                ],
                "matching_skills": ["Python", "FastAPI", "AWS"],
                "missing_skills": [],
                "experience_highlights": ["Delivered production APIs for enterprise banking clients in Abu Dhabi"],
                "concerns": [],
                "summary": "Strong production profile with partial salary clarity.",
            }
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(payload))],
                usage=SimpleNamespace(input_tokens=160, output_tokens=140),
            )

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))

    result = calculate_cv_job_match_sync(
        cv_text=(
            "Senior engineer with 8 years in production. "
            "Delivered production APIs for enterprise banking clients in Abu Dhabi. "
            "Led incident response and on-call for mission-critical services."
        ),
        job_spec_text="Need enterprise production experience and compensation fit.",
        api_key="test-key",
        model=PRIMARY_HAIKU_MODEL,
        additional_requirements=(
            "Production experience with enterprise systems; "
            "Salary 25k-35k AED target"
        ),
    )

    requirements = result["match_details"]["requirements_assessment"]
    assert len(requirements) == 2
    assert requirements[0]["evidence"].startswith("CV evidence:")
    assert "enterprise" in requirements[0]["evidence"].lower()
    assert requirements[1]["evidence"] != "Good fit overall."
    assert requirements[1]["impact"] != ""
    assert result["match_details"]["scoring_version"] == "cv_fit_v3_evidence_enriched"
