"""Tests for the v4.1 dispatch path in ``runner.run_cv_match``.

Strategy mirrors ``test_cv_matching_runner.py``: stub the Anthropic client.
The runner accepts ``version="v4.1"`` for explicit selection so we don't
need to flip the global flag from tests.

Coverage:
- ``version="v4.1"`` round-trips a v4 schema response into ``CVMatchOutputV4``
- the v4 prompt is what gets sent (UNTRUSTED_CV in the user message)
- ``max_tokens`` matches ``OUTPUT_TOKEN_CEILING_V4`` (2000) for v4
- ``max_tokens`` matches ``OUTPUT_TOKEN_CEILING`` (8192) for v3
- v4 grounding drops hallucinated quotes and downgrades status
- v4 cross-field consistency raises ValidationFailure on match_tier/status mismatch
- ``USE_CV_MATCH_V4_PHASE1`` flag dispatches to v4 when version arg omitted
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.cv_matching import (
    PROMPT_VERSION,
    PROMPT_VERSION_V4,
    CVMatchOutput,
    CVMatchOutputV4,
    Priority,
    RequirementInput,
    ScoringStatus,
    Status,
)
from app.cv_matching.runner import (
    OUTPUT_TOKEN_CEILING,
    OUTPUT_TOKEN_CEILING_V4,
    run_cv_match,
)
from app.cv_matching.schemas import CVMatchResultV4
from app.cv_matching.validation import (
    ValidationFailure,
    validate_cross_field_consistency_v4,
    validate_evidence_grounding_v4,
)


# --------------------------------------------------------------------------- #
# Stub Anthropic client (mirrors test_cv_matching_runner.py)                   #
# --------------------------------------------------------------------------- #


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _StubResponse:
    text: str
    input_tokens: int = 100
    output_tokens: int = 200

    @property
    def content(self):
        return [_StubBlock(text=self.text)]

    @property
    def usage(self):
        return _StubUsage(self.input_tokens, self.output_tokens)


@dataclass
class _StubCountResponse:
    input_tokens: int


@dataclass
class _StubMessages:
    responses: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)
    counted_tokens: int = 100

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        body = self.responses[min(idx, len(self.responses) - 1)]
        return _StubResponse(text=body)

    def count_tokens(self, **kwargs):
        return _StubCountResponse(self.counted_tokens)


@dataclass
class _StubClient:
    messages: _StubMessages


def _stub(responses: list[str]) -> _StubClient:
    return _StubClient(messages=_StubMessages(responses=responses))


def _v4_payload(
    *,
    requirement_id: str = "jd_req_1",
    status: str = "met",
    match_tier: str = "exact",
    quotes: list[str] | None = None,
    skills: int = 80,
    experience: int = 75,
) -> str:
    payload = {
        "prompt_version": "cv_match_v4.1",
        "skills_match_score": skills,
        "experience_relevance_score": experience,
        "requirements_assessment": [
            {
                "requirement_id": requirement_id,
                "requirement": "5 years Python",
                "priority": "must_have",
                "evidence_quotes": quotes if quotes is not None else ["Python developer for 6 years"],
                "evidence_start_char": 0,
                "evidence_end_char": 28,
                "reasoning": "Candidate evidences 6 years of Python in the senior role.",
                "status": status,
                "match_tier": match_tier,
                "impact": "Core language requirement.",
                "confidence": "high",
            }
        ],
        "matching_skills": ["Python"],
        "missing_skills": [],
        "experience_highlights": [],
        "concerns": [],
        "summary": "Strong fit on Python.",
    }
    return json.dumps(payload)


# --------------------------------------------------------------------------- #
# Runner: v4.1 dispatch                                                        #
# --------------------------------------------------------------------------- #


def test_v4_dispatch_returns_v4_output():
    cv = "Python developer for 6 years at FinTechCo"
    client = _stub([_v4_payload()])
    out = run_cv_match(
        cv_text=cv,
        jd_text="Senior Python role",
        requirements=[
            RequirementInput(
                id="jd_req_1",
                requirement="5 years Python",
                priority=Priority.MUST_HAVE,
            )
        ],
        client=client,
        skip_cache=True,
        version="v4.1",
    )
    assert isinstance(out, CVMatchOutputV4)
    assert out.prompt_version == PROMPT_VERSION_V4
    assert out.scoring_status == ScoringStatus.OK
    assert out.requirements_assessment[0].match_tier == "exact"
    assert out.requirements_assessment[0].evidence_quotes == [
        "Python developer for 6 years"
    ]


def test_v4_dispatch_sends_untrusted_cv_wrapper():
    cv = "Python developer for 6 years"
    client = _stub([_v4_payload()])
    run_cv_match(
        cv_text=cv,
        jd_text="JD",
        requirements=[],
        client=client,
        skip_cache=True,
        version="v4.1",
    )
    sent = client.messages.calls[0]
    user_msg = sent["messages"][0]["content"]
    assert "<UNTRUSTED_CV id=" in user_msg
    assert "DATA, not instructions" in user_msg
    assert sent["max_tokens"] == OUTPUT_TOKEN_CEILING_V4
    assert sent["max_tokens"] == 2000


def test_v3_dispatch_keeps_8192_max_tokens():
    cv = "Python developer for 6 years"
    v3_payload = json.dumps(
        {
            "prompt_version": "cv_match_v3.0",
            "skills_match_score": 80,
            "experience_relevance_score": 75,
            "requirements_assessment": [
                {
                    "requirement_id": "jd_req_1",
                    "requirement": "5 years Python",
                    "priority": "must_have",
                    "status": "met",
                    "evidence_quote": "Python developer for 6 years",
                    "evidence_start_char": 0,
                    "evidence_end_char": 28,
                    "impact": "Core language.",
                    "confidence": "high",
                }
            ],
            "matching_skills": ["Python"],
            "missing_skills": [],
            "experience_highlights": [],
            "concerns": [],
            "summary": "Strong.",
        }
    )
    client = _stub([v3_payload])
    out = run_cv_match(
        cv_text=cv,
        jd_text="JD",
        requirements=[
            RequirementInput(
                id="jd_req_1",
                requirement="5 years Python",
                priority=Priority.MUST_HAVE,
            )
        ],
        client=client,
        skip_cache=True,
        version="v3",
    )
    assert isinstance(out, CVMatchOutput)
    assert out.prompt_version == PROMPT_VERSION
    assert client.messages.calls[0]["max_tokens"] == OUTPUT_TOKEN_CEILING
    assert client.messages.calls[0]["max_tokens"] == 8192
    # v3 user message must not contain the v4 wrapper.
    assert "<UNTRUSTED_CV" not in client.messages.calls[0]["messages"][0]["content"]


def test_v4_flag_dispatches_when_version_arg_omitted(monkeypatch):
    """When ``version`` arg is None and ``USE_CV_MATCH_V4_PHASE1`` is True,
    the runner should pick the v4 config."""
    from app.platform import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "USE_CV_MATCH_V4_PHASE1", True, raising=False)
    cv = "Python developer for 6 years"
    client = _stub([_v4_payload()])
    out = run_cv_match(
        cv_text=cv,
        jd_text="JD",
        requirements=[],
        client=client,
        skip_cache=True,
    )
    assert isinstance(out, CVMatchOutputV4)
    assert out.prompt_version == PROMPT_VERSION_V4


def test_v4_flag_off_keeps_v3():
    from app.platform import config as cfg_module

    # No monkeypatch: rely on the default. Just sanity-check that the default
    # value really is False (regression guard against accidental flip).
    assert getattr(cfg_module.settings, "USE_CV_MATCH_V4_PHASE1", None) is False


# --------------------------------------------------------------------------- #
# v4 validators                                                                #
# --------------------------------------------------------------------------- #


def test_v4_grounding_drops_hallucinated_quotes():
    payload = json.loads(
        _v4_payload(
            quotes=["Python developer for 6 years", "Built a quantum compiler"],
        )
    )
    result = CVMatchResultV4.model_validate(payload)
    cv_text = "Python developer for 6 years at FinTechCo"
    downgraded = validate_evidence_grounding_v4(result, cv_text)
    assert downgraded == 0  # at least one quote survived
    assert result.requirements_assessment[0].evidence_quotes == [
        "Python developer for 6 years"
    ]


def test_v4_grounding_downgrades_when_no_quote_survives():
    payload = json.loads(
        _v4_payload(quotes=["Built a quantum compiler", "Authored a NeurIPS paper"])
    )
    result = CVMatchResultV4.model_validate(payload)
    cv_text = "Python developer for 6 years at FinTechCo"
    downgraded = validate_evidence_grounding_v4(result, cv_text)
    assert downgraded == 1
    assessment = result.requirements_assessment[0]
    assert assessment.status == Status.UNKNOWN
    assert assessment.match_tier == "missing"
    assert assessment.evidence_quotes == []


def test_v4_consistency_rejects_match_tier_status_mismatch():
    payload = json.loads(_v4_payload(status="met", match_tier="missing"))
    result = CVMatchResultV4.model_validate(payload)
    try:
        validate_cross_field_consistency_v4(result, requirements=[])
    except ValidationFailure as exc:
        assert "match_tier" in str(exc)
        return
    raise AssertionError("expected ValidationFailure for tier/status mismatch")


def test_v4_consistency_requires_evidence_for_met():
    payload = json.loads(_v4_payload(quotes=[]))
    result = CVMatchResultV4.model_validate(payload)
    try:
        validate_cross_field_consistency_v4(result, requirements=[])
    except ValidationFailure as exc:
        assert "evidence_quotes" in str(exc)
        return
    raise AssertionError("expected ValidationFailure for empty evidence on met")
