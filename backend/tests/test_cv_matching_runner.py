"""Tests for the unified ``runner.run_cv_match`` (single scoring path)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.cv_matching import (
    CVMatchOutput,
    PROMPT_VERSION,
    Priority,
    RequirementInput,
    ScoringStatus,
    Status,
)
from app.cv_matching import archetype_synthesizer
from app.cv_matching.runner import run_cv_match
from app.cv_matching.schemas import CVMatchResult
from app.cv_matching.validation import (
    ValidationFailure,
    validate_cross_field_consistency,
    validate_evidence_grounding,
)


# --------------------------------------------------------------------------- #
# Stub Anthropic client                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubUsage:
    input_tokens: int = 100
    output_tokens: int = 200


@dataclass
class _StubResponse:
    text: str

    @property
    def content(self):
        return [_StubBlock(text=self.text)]

    @property
    def usage(self):
        return _StubUsage()


@dataclass
class _StubMessages:
    responses: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        body = self.responses[min(idx, len(self.responses) - 1)]
        return _StubResponse(text=body)

    def count_tokens(self, **kwargs):
        @dataclass
        class _C:
            input_tokens: int = 100

        return _C()


@dataclass
class _StubClient:
    messages: _StubMessages


def _stub_client(responses: list[str]) -> _StubClient:
    return _StubClient(messages=_StubMessages(responses=responses))


def _payload(
    *,
    cv_text: str = "Python developer for 6 years",
    quotes: list[str] | None = None,
    status: str = "met",
    match_tier: str = "exact",
) -> str:
    return json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "dimension_scores": {
                "skills_coverage": 80.0,
                "skills_depth": 75.0,
                "title_trajectory": 70.0,
                "seniority_alignment": 65.0,
                "industry_match": 60.0,
                "tenure_pattern": 55.0,
            },
            "skills_match_score": 0,
            "experience_relevance_score": 0,
            "requirements_assessment": [
                {
                    "requirement_id": "jd_req_1",
                    "requirement": "5+ years Python",
                    "priority": "must_have",
                    "evidence_quotes": quotes if quotes is not None else [cv_text],
                    "evidence_start_char": 0,
                    "evidence_end_char": len(cv_text),
                    "reasoning": "Candidate evidences Python.",
                    "status": status,
                    "match_tier": match_tier,
                    "impact": "Core requirement.",
                    "confidence": "high",
                }
            ],
            "matching_skills": ["Python"],
            "missing_skills": [],
            "experience_highlights": [],
            "concerns": [],
            "summary": "Strong fit.",
        }
    )


def _disable_archetype(monkeypatch):
    """Force the archetype synthesizer to return None so tests stay
    deterministic (no extra Sonnet stub call to set up)."""
    monkeypatch.setattr(
        archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None
    )


# --------------------------------------------------------------------------- #
# Pipeline                                                                     #
# --------------------------------------------------------------------------- #


def test_runner_returns_canonical_output(monkeypatch):
    _disable_archetype(monkeypatch)
    cv = "Python developer for 6 years"
    client = _stub_client([_payload(cv_text=cv)])
    out = run_cv_match(
        cv_text=cv,
        jd_text="Senior Python role",
        requirements=[
            RequirementInput(
                id="jd_req_1",
                requirement="5+ years Python",
                priority=Priority.MUST_HAVE,
            )
        ],
        client=client,
        skip_cache=True,
    )
    assert isinstance(out, CVMatchOutput)
    assert out.scoring_status == ScoringStatus.OK
    assert out.prompt_version == PROMPT_VERSION
    assert out.requirements_assessment[0].match_tier == "exact"
    assert out.dimension_scores is not None
    # v3-compat back-fill from dimensions:
    # skills = mean(80, 75) = 77.5; experience = mean(70, 65, 60, 55) = 62.5
    assert abs(out.skills_match_score - 77.5) < 0.01
    assert abs(out.experience_relevance_score - 62.5) < 0.01


def test_runner_sends_untrusted_cv_wrapper(monkeypatch):
    _disable_archetype(monkeypatch)
    cv = "Python developer for 6 years"
    client = _stub_client([_payload(cv_text=cv)])
    run_cv_match(
        cv_text=cv,
        jd_text="JD",
        requirements=[],
        client=client,
        skip_cache=True,
    )
    sent = client.messages.calls[0]
    content_blocks = sent["messages"][0]["content"]
    # content is now a list of blocks: [cached static block, dynamic CV block]
    all_text = "".join(b["text"] for b in content_blocks if isinstance(b, dict))
    assert "<UNTRUSTED_CV id=" in all_text
    assert "DATA, not instructions" in all_text
    # CV content must be in the dynamic (non-cached) block only
    cv_block = content_blocks[1]
    assert "<UNTRUSTED_CV id=" in cv_block["text"]
    assert "cache_control" not in cv_block


def test_runner_failure_on_input_token_ceiling(monkeypatch):
    _disable_archetype(monkeypatch)
    cv = "x"
    client = _stub_client([_payload()])
    # Force count_tokens to report way over the 3500 ceiling.
    client.messages.counted_tokens = 99999  # type: ignore[attr-defined]

    @dataclass
    class _FatCount:
        input_tokens: int = 99999

    def fat_count(**kwargs):
        return _FatCount()

    client.messages.count_tokens = fat_count  # type: ignore[method-assign]

    out = run_cv_match(
        cv_text=cv, jd_text="JD", requirements=[], client=client, skip_cache=True
    )
    assert out.scoring_status == ScoringStatus.FAILED
    assert "input_token_ceiling_exceeded" in out.error_reason


def test_runner_failure_on_invalid_json_after_retry(monkeypatch):
    _disable_archetype(monkeypatch)
    client = _stub_client(["not json", "still not json"])
    out = run_cv_match(
        cv_text="cv", jd_text="JD", requirements=[], client=client, skip_cache=True
    )
    assert out.scoring_status == ScoringStatus.FAILED
    assert "validation_failed_after_retry" in out.error_reason


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


def test_grounding_drops_hallucinated_quotes():
    payload = json.loads(
        _payload(quotes=["Python developer for 6 years", "Built a quantum compiler"])
    )
    result = CVMatchResult.model_validate(payload)
    cv_text = "Python developer for 6 years at FinTechCo"
    downgraded = validate_evidence_grounding(result, cv_text)
    assert downgraded == 0
    assert result.requirements_assessment[0].evidence_quotes == [
        "Python developer for 6 years"
    ]


def test_grounding_downgrades_when_no_quote_survives():
    payload = json.loads(
        _payload(quotes=["Built a quantum compiler", "Authored a NeurIPS paper"])
    )
    result = CVMatchResult.model_validate(payload)
    cv_text = "Python developer for 6 years at FinTechCo"
    downgraded = validate_evidence_grounding(result, cv_text)
    assert downgraded == 1
    assert result.requirements_assessment[0].status == Status.UNKNOWN
    assert result.requirements_assessment[0].match_tier == "missing"


def test_consistency_rejects_match_tier_status_mismatch():
    payload = json.loads(_payload(status="met", match_tier="missing"))
    result = CVMatchResult.model_validate(payload)
    try:
        validate_cross_field_consistency(result, requirements=[])
    except ValidationFailure as exc:
        assert "match_tier" in str(exc)
        return
    raise AssertionError("expected ValidationFailure for tier/status mismatch")


def test_consistency_requires_evidence_for_met():
    payload = json.loads(_payload(quotes=[]))
    result = CVMatchResult.model_validate(payload)
    try:
        validate_cross_field_consistency(result, requirements=[])
    except ValidationFailure as exc:
        assert "evidence_quotes" in str(exc)
        return
    raise AssertionError("expected ValidationFailure for empty evidence on met")
