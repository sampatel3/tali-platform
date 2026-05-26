"""Tests for the unified ``runner.run_cv_match`` (single scoring path).

The runner runs in forced tool-use mode (Phase 2), so the pipeline-test
stubs return ``tool_use`` blocks carrying ``CVMatchResult`` payload dicts
as the tool's ``.input``. The ``_text()`` helper is kept for the negative
tests that simulate a model that emitted prose instead of using the tool.

The validation tests (grounding / consistency / partial-coverage) do not
exercise the runner — they construct ``CVMatchResult`` directly and call
the validators — so the tool-use flip doesn't touch them.
"""

from __future__ import annotations

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

# Tool name derived by the gateway from ``CVMatchResult``. Stable across
# calls so the prompt-cached tool definition stays warm.
TOOL_NAME = "emit_cv_match_result"


@dataclass
class _StubBlock:
    text: str


@dataclass
class _ToolUseBlock:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class _StubUsage:
    input_tokens: int = 100
    output_tokens: int = 200


@dataclass
class _StubResponse:
    """Anthropic-shaped response carrying arbitrary content blocks."""

    blocks: list[Any]

    @property
    def content(self):
        return self.blocks

    @property
    def usage(self):
        return _StubUsage()


def _text(text: str) -> _StubResponse:
    """Response with a single text block — simulates a model that emitted
    prose instead of using the tool (the gateway treats this as a
    ``ValidationFailure`` and retries)."""
    return _StubResponse(blocks=[_StubBlock(text=text)])


def _tu(input_dict: dict, name: str = TOOL_NAME) -> _StubResponse:
    """Response with a single ``tool_use`` block carrying ``CVMatchResult``
    fields as the tool's ``.input`` dict (the happy path)."""
    return _StubResponse(blocks=[_ToolUseBlock(name=name, input=input_dict)])


@dataclass
class _StubMessages:
    responses: list[_StubResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        return self.responses[min(idx, len(self.responses) - 1)]

    def count_tokens(self, **kwargs):
        @dataclass
        class _C:
            input_tokens: int = 100

        return _C()


@dataclass
class _StubClient:
    messages: _StubMessages


def _stub_client(*responses: _StubResponse) -> _StubClient:
    return _StubClient(messages=_StubMessages(responses=list(responses)))


def _payload(
    *,
    cv_text: str = "Python developer for 6 years",
    quotes: list[str] | None = None,
    status: str = "met",
    match_tier: str = "exact",
) -> dict:
    """Build a CVMatchResult payload dict (used as tool_use input AND in
    direct validation tests via ``CVMatchResult.model_validate``)."""
    return {
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
    client = _stub_client(_tu(_payload(cv_text=cv)))
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

    # Forced tool-use: gateway sent a single synthetic tool whose
    # input_schema is CVMatchResult.model_json_schema().
    sent = client.messages.calls[0]
    assert sent["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert sent["tools"][0]["name"] == TOOL_NAME
    assert sent["tools"][0]["input_schema"]["type"] == "object"


def _payload_with_integrity(
    *,
    claims: list[dict] | None = None,
    timeline: list[dict] | None = None,
) -> dict:
    cv = "Python developer for 6 years"
    body = _payload(cv_text=cv)
    if timeline is not None:
        body["candidate_snapshot"] = {
            "years_experience": 6,
            "top_skills": ["Python"],
            "timeline": timeline,
        }
    if claims is not None:
        body["claims_to_verify"] = claims
    return body


def _run_with_payload(monkeypatch, payload: dict) -> CVMatchOutput:
    _disable_archetype(monkeypatch)
    return run_cv_match(
        cv_text="Python developer for 6 years",
        jd_text="Senior Python role",
        requirements=[
            RequirementInput(
                id="jd_req_1", requirement="5+ years Python", priority=Priority.MUST_HAVE
            )
        ],
        client=_stub_client(_tu(payload)),
        skip_cache=True,
    )


def test_runner_clean_cv_has_no_integrity_penalty(monkeypatch):
    out = _run_with_payload(monkeypatch, _payload_with_integrity(claims=[], timeline=[]))
    assert out.integrity_penalty == 0.0
    assert out.claims_to_verify == []
    assert out.timeline_flags == []


def test_runner_penalises_unverified_claim_and_timeline(monkeypatch):
    clean = _run_with_payload(monkeypatch, _payload_with_integrity(claims=[], timeline=[]))
    flagged = _run_with_payload(
        monkeypatch,
        _payload_with_integrity(
            claims=[
                {
                    "claim_text": "1st place, XYZ Global Hackathon 2023",
                    "claim_type": "competition",
                    "corroboration": "uncorroborated",
                    "model_familiarity": "unknown",
                    "reasoning": "No employer/date context; event unknown.",
                }
            ],
            timeline=[
                {
                    "company": "Acme",
                    "role": "Engineer",
                    "start_year": 2020,
                    "end_year": 2015,
                    "is_current": False,
                }
            ],
        ),
    )
    # 1 unverified claim + 1 timeline issue = 2 × 5pts default.
    assert flagged.integrity_penalty == 10.0
    assert len(flagged.claims_to_verify) == 1
    assert flagged.claims_to_verify[0].claim_type == "competition"
    assert len(flagged.timeline_flags) == 1
    assert "before it starts" in flagged.timeline_flags[0]
    # The operative score (role_fit) reflects the deduction.
    assert flagged.role_fit_score == max(0.0, clean.role_fit_score - 10.0)


def test_runner_does_not_penalise_corroborated_known_claim(monkeypatch):
    out = _run_with_payload(
        monkeypatch,
        _payload_with_integrity(
            claims=[
                {
                    "claim_text": "Won Google Summer of Code 2019",
                    "claim_type": "award",
                    "corroboration": "corroborated",
                    "model_familiarity": "known",
                    "reasoning": "Well-known program; CV gives the year.",
                }
            ],
            timeline=[],
        ),
    )
    # Still surfaced for the recruiter, but no score deduction.
    assert out.integrity_penalty == 0.0
    assert len(out.claims_to_verify) == 1


def test_runner_sends_untrusted_cv_wrapper(monkeypatch):
    _disable_archetype(monkeypatch)
    cv = "Python developer for 6 years"
    client = _stub_client(_tu(_payload(cv_text=cv)))
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
    import app.cv_matching.runner as runner_module

    # Drive the ceiling to 1 so even a tiny input exceeds it.
    monkeypatch.setattr(runner_module, "INPUT_TOKEN_CEILING", 1)
    cv = "x"
    client = _stub_client(_tu(_payload()))
    out = run_cv_match(
        cv_text=cv, jd_text="JD", requirements=[], client=client, skip_cache=True
    )
    assert out.scoring_status == ScoringStatus.FAILED
    assert "input_token_ceiling_exceeded" in out.error_reason


def test_runner_failure_when_model_never_uses_the_tool(monkeypatch):
    """Both attempts emit prose instead of using the forced tool → runner
    surfaces ``validation_failed_after_retry``. Equivalent to the old
    'invalid JSON twice' case under tool-use semantics."""
    _disable_archetype(monkeypatch)
    client = _stub_client(_text("no tool here"), _text("still no tool"))
    out = run_cv_match(
        cv_text="cv", jd_text="JD", requirements=[], client=client, skip_cache=True
    )
    assert out.scoring_status == ScoringStatus.FAILED
    assert "validation_failed_after_retry" in out.error_reason


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


def test_grounding_drops_hallucinated_quotes():
    payload = _payload(quotes=["Python developer for 6 years", "Built a quantum compiler"])
    result = CVMatchResult.model_validate(payload)
    cv_text = "Python developer for 6 years at FinTechCo"
    downgraded = validate_evidence_grounding(result, cv_text)
    assert downgraded == 0
    assert result.requirements_assessment[0].evidence_quotes == [
        "Python developer for 6 years"
    ]


def test_grounding_downgrades_when_no_quote_survives():
    payload = _payload(quotes=["Built a quantum compiler", "Authored a NeurIPS paper"])
    result = CVMatchResult.model_validate(payload)
    cv_text = "Python developer for 6 years at FinTechCo"
    downgraded = validate_evidence_grounding(result, cv_text)
    assert downgraded == 1
    assert result.requirements_assessment[0].status == Status.UNKNOWN
    assert result.requirements_assessment[0].match_tier == "missing"


def test_consistency_rejects_match_tier_status_mismatch():
    payload = _payload(status="met", match_tier="missing")
    result = CVMatchResult.model_validate(payload)
    try:
        validate_cross_field_consistency(result, requirements=[])
    except ValidationFailure as exc:
        assert "match_tier" in str(exc)
        return
    raise AssertionError("expected ValidationFailure for tier/status mismatch")


def test_consistency_requires_evidence_for_met():
    payload = _payload(quotes=[])
    result = CVMatchResult.model_validate(payload)
    try:
        validate_cross_field_consistency(result, requirements=[])
    except ValidationFailure as exc:
        assert "evidence_quotes" in str(exc)
        return
    raise AssertionError("expected ValidationFailure for empty evidence on met")


# --------------------------------------------------------------------------- #
# Partial-coverage handling — 2026-05-22 cost optimisation                     #
# --------------------------------------------------------------------------- #
#
# Before: the validator raised ``ValidationFailure`` whenever ANY recruiter
# requirement_id was missing from the assessment, forcing the runner to
# retry (another Anthropic call) and, if the retry failed the same way,
# error out the entire scoring run. On 2026-05-21 this fired on 3,281 of
# 3,918 scoring attempts (84%), consuming roughly $41 of Haiku spend for
# zero usable output. Most failures were 1-2 missing criteria out of
# many — the model wasn't broken, the validator was too strict.
#
# After: if fewer than 50% of recruiter requirements are missing, the
# validator SYNTHESISES UNKNOWN placeholders for the gaps. The recruiter
# sees the partial assessment with un-assessed criteria flagged.
# ValidationFailure still fires when more than half the criteria are
# absent — that's a genuine model failure worth a retry.


from app.cv_matching.schemas import (  # noqa: E402
    Category,
    Priority,
    RequirementInput,
)


def _req(req_id: str, priority: str = "must_have") -> RequirementInput:
    return RequirementInput(
        id=req_id,
        requirement="some requirement",
        priority=Priority(priority),
        category=Category.TECHNICAL_SKILL,
    )


def _payload_with_assessment_ids(ids: list[str]) -> dict:
    """Build a CVMatchResult payload covering exactly the listed ids."""
    return {
        "prompt_version": PROMPT_VERSION,
        "dimension_scores": {
            "skills_coverage": 70.0,
            "skills_depth": 70.0,
            "title_trajectory": 70.0,
            "seniority_alignment": 70.0,
            "industry_match": 70.0,
            "tenure_pattern": 70.0,
        },
        "skills_match_score": 70,
        "experience_relevance_score": 70,
        "requirements_assessment": [
            {
                "requirement_id": rid,
                "requirement": "x",
                "priority": "must_have",
                "evidence_quotes": ["evidence"],
                "evidence_start_char": 0,
                "evidence_end_char": 8,
                "reasoning": "ok",
                "status": "met",
                "match_tier": "exact",
                "impact": "Core requirement.",
                "confidence": "high",
            }
            for rid in ids
        ],
        "matching_skills": [],
        "missing_skills": [],
        "experience_highlights": [],
        "concerns": [],
        "summary": "Partial assessment.",
    }


def test_partial_missing_criteria_synthesises_unknown_placeholders():
    """Model assessed 3 of 4 required criteria. Previously: hard reject
    + retry. Now: validator fills the gap with an UNKNOWN placeholder so
    the scoring run lands instead of burning a retry call."""
    result = CVMatchResult.model_validate(_payload_with_assessment_ids(["r1", "r2", "r3"]))
    requirements = [_req("r1"), _req("r2"), _req("r3"), _req("r4")]

    # Should NOT raise.
    validate_cross_field_consistency(result, requirements=requirements)

    # The result now has all 4 assessments, with the missing one
    # synthesised.
    assessed_ids = {a.requirement_id for a in result.requirements_assessment}
    assert assessed_ids == {"r1", "r2", "r3", "r4"}
    synthesised = next(a for a in result.requirements_assessment if a.requirement_id == "r4")
    assert synthesised.status == Status.UNKNOWN
    assert synthesised.match_tier == "missing"
    assert synthesised.evidence_quotes == []
    assert "not assessed by model" in synthesised.reasoning


def test_severe_missing_still_raises_for_retry():
    """If the model omits more than 50% of criteria, the validator still
    raises — that's a real model failure worth a retry, not just a
    slightly forgetful response."""
    result = CVMatchResult.model_validate(_payload_with_assessment_ids(["r1"]))
    requirements = [_req(f"r{i}") for i in range(1, 5)]  # r1..r4, 3 missing

    try:
        validate_cross_field_consistency(result, requirements=requirements)
    except ValidationFailure as exc:
        assert "severe" in str(exc)
        return
    raise AssertionError("expected ValidationFailure for >50% missing criteria")


def test_all_criteria_assessed_no_synthesis():
    """Sanity: the happy path is unchanged when the model covered every
    requirement."""
    result = CVMatchResult.model_validate(_payload_with_assessment_ids(["r1", "r2"]))
    requirements = [_req("r1"), _req("r2")]

    validate_cross_field_consistency(result, requirements=requirements)
    assert len(result.requirements_assessment) == 2
    for a in result.requirements_assessment:
        assert "not assessed" not in a.reasoning
