"""Tests for archetype routing (RALPH 2.7) and v4.2 prompt (RALPH 2.8)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.cv_matching import (
    CVMatchOutputV4,
    PROMPT_VERSION_V4_2,
    Priority,
    RequirementInput,
    ScoringStatus,
)
from app.cv_matching import archetype_router
from app.cv_matching.archetype_router import (
    DEFAULT_THRESHOLD,
    pick_archetype,
    reset_cache,
)
from app.cv_matching.embeddings import clear_cache as clear_embed_cache
from app.cv_matching.prompts import (
    CV_MATCH_PROMPT_V4_2,
    build_cv_match_prompt_v4_2,
    render_archetype_block,
)
from app.cv_matching.rubrics import load_rubric
from app.cv_matching.runner import run_cv_match


# --------------------------------------------------------------------------- #
# Stub Anthropic client (mirrors test_cv_matching_v4_runner.py)                #
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
        return _StubResponse(text=self.responses[min(idx, len(self.responses) - 1)])

    def count_tokens(self, **_):
        @dataclass
        class _C:
            input_tokens: int = 100

        return _C()


@dataclass
class _StubClient:
    messages: _StubMessages


def setup_function(_):
    clear_embed_cache()
    reset_cache()


# --------------------------------------------------------------------------- #
# Archetype router                                                             #
# --------------------------------------------------------------------------- #


def test_pick_archetype_returns_match_for_centroid_text():
    """The aws_glue_data_engineer rubric's own jd_centroid_text should
    self-match at cosine 1.0 — i.e. the router picks it confidently."""
    rubric = load_rubric("aws_glue_data_engineer")
    match = pick_archetype(rubric.jd_centroid_text, requirements=[])
    assert match is not None
    assert match.rubric.archetype_id == "aws_glue_data_engineer"
    assert match.similarity > 0.99  # near-self-match


def test_pick_archetype_returns_none_when_no_match_clears_threshold():
    """A wildly unrelated JD should return None at the default threshold."""
    match = pick_archetype(
        jd_text="We need a barista who can pull a perfect espresso.",
        requirements=[],
        threshold=0.99,  # impossibly tight to force a None
    )
    # The mock embed produces non-trivially similar vectors at low
    # threshold, so we force the threshold up to assert None-handling.
    assert match is None


def test_pick_archetype_with_lower_threshold_returns_best():
    match = pick_archetype(
        jd_text="completely off-topic text",
        requirements=[],
        threshold=-1.0,  # always pick *something*
    )
    assert match is not None
    assert match.rubric.archetype_id  # at minimum, *some* archetype


def test_pick_archetype_threshold_default_is_55():
    assert DEFAULT_THRESHOLD == 0.55


# --------------------------------------------------------------------------- #
# render_archetype_block                                                       #
# --------------------------------------------------------------------------- #


def test_render_archetype_block_empty_when_none():
    assert render_archetype_block(None) == ""


def test_render_archetype_block_includes_substitution_rules():
    rubric = load_rubric("aws_glue_data_engineer")
    block = render_archetype_block(rubric)
    assert "ARCHETYPE CONTEXT" in block
    assert "aws_glue_data_engineer" in block
    assert "managed_spark_etl" in block
    assert "Exact match terms:" in block
    assert "Strong substitutes" in block
    assert "Weak substitutes" in block
    assert "AWS Glue" in block
    assert "Anchored seniority bands" in block


# --------------------------------------------------------------------------- #
# v4.2 prompt builder                                                          #
# --------------------------------------------------------------------------- #


def test_v4_2_prompt_no_archetype_renders_clean():
    out = build_cv_match_prompt_v4_2(
        cv_text="x", jd_text="y", requirements=[], archetype=None
    )
    assert "<UNTRUSTED_CV id=" in out
    assert "ARCHETYPE CONTEXT" not in out  # empty when archetype=None
    assert "prompt_version: cv_match_v4.2" in out


def test_v4_2_prompt_with_archetype_includes_block():
    rubric = load_rubric("aws_glue_data_engineer")
    out = build_cv_match_prompt_v4_2(
        cv_text="x", jd_text="y", requirements=[], archetype=rubric
    )
    assert "ARCHETYPE CONTEXT" in out
    assert "aws_glue_data_engineer" in out


def test_v4_2_prompt_template_constant_referenced():
    assert "cv_match_v4.2" in CV_MATCH_PROMPT_V4_2


# --------------------------------------------------------------------------- #
# Runner v4.2 dispatch                                                         #
# --------------------------------------------------------------------------- #


def _v4_payload() -> str:
    return json.dumps(
        {
            "prompt_version": "cv_match_v4.2",
            "skills_match_score": 80,
            "experience_relevance_score": 75,
            "requirements_assessment": [
                {
                    "requirement_id": "jd_req_1",
                    "requirement": "AWS Glue ETL ownership",
                    "priority": "must_have",
                    "evidence_quotes": [
                        "Led AWS Glue / Spark ETL platform for 3 years"
                    ],
                    "evidence_start_char": 0,
                    "evidence_end_char": 47,
                    "reasoning": "Cluster managed_spark_etl: exact match — AWS Glue named.",
                    "status": "met",
                    "match_tier": "exact",
                    "impact": "Core requirement met.",
                    "confidence": "high",
                }
            ],
            "matching_skills": ["AWS Glue"],
            "missing_skills": [],
            "experience_highlights": [],
            "concerns": [],
            "summary": "Strong fit on Glue ownership.",
        }
    )


def test_v4_2_dispatch_routes_glue_jd_to_archetype():
    """A JD that's basically the aws_glue centroid should trigger archetype
    routing and surface that archetype block in the prompt sent to Claude.

    We pass requirements=[] so the JD-embed text equals the centroid-
    embed text exactly — cosine ~1.0 on the deterministic mock provider.
    Production with the real Voyage provider does NOT need this caveat;
    Voyage embeddings are robust to small textual perturbations.
    """
    rubric = load_rubric("aws_glue_data_engineer")
    cv = "Led AWS Glue / Spark ETL platform for 3 years"
    # Payload references a JD-extracted requirement (jd_req_1) so the
    # consistency check passes when we send empty recruiter requirements.
    payload = json.loads(_v4_payload())
    payload["requirements_assessment"][0]["requirement_id"] = "jd_req_1"
    client = _StubClient(messages=_StubMessages(responses=[json.dumps(payload)]))

    out = run_cv_match(
        cv_text=cv,
        jd_text=rubric.jd_centroid_text,
        requirements=[],
        client=client,
        skip_cache=True,
        version="v4.2",
    )
    assert isinstance(out, CVMatchOutputV4)
    assert out.scoring_status == ScoringStatus.OK
    assert out.prompt_version == PROMPT_VERSION_V4_2

    sent_user = client.messages.calls[0]["messages"][0]["content"]
    assert "ARCHETYPE CONTEXT" in sent_user
    assert "aws_glue_data_engineer" in sent_user


def test_v4_2_dispatch_falls_back_when_no_archetype_matches(monkeypatch):
    """When the router returns None (no archetype above threshold), the
    v4.2 prompt should still render — just without an archetype block."""
    monkeypatch.setattr(
        archetype_router,
        "pick_archetype",
        lambda jd_text, requirements=None, threshold=DEFAULT_THRESHOLD, embed_fn=None: None,
    )
    cv = "Led AWS Glue / Spark ETL platform for 3 years"
    client = _StubClient(messages=_StubMessages(responses=[_v4_payload()]))
    out = run_cv_match(
        cv_text=cv,
        jd_text="completely unrelated JD about poetry",
        requirements=[
            RequirementInput(
                id="jd_req_1",
                requirement="AWS Glue ETL ownership",
                priority=Priority.MUST_HAVE,
            )
        ],
        client=client,
        skip_cache=True,
        version="v4.2",
    )
    assert isinstance(out, CVMatchOutputV4)
    sent_user = client.messages.calls[0]["messages"][0]["content"]
    assert "ARCHETYPE CONTEXT" not in sent_user


def test_phase2_flag_dispatches_to_v4_2(monkeypatch):
    from app.platform import config as cfg_module

    monkeypatch.setattr(
        cfg_module.settings, "USE_CV_MATCH_V4_PHASE2", True, raising=False
    )
    cv = "Led AWS Glue / Spark ETL platform for 3 years"
    client = _StubClient(messages=_StubMessages(responses=[_v4_payload()]))
    out = run_cv_match(
        cv_text=cv,
        jd_text="JD",
        requirements=[],
        client=client,
        skip_cache=True,
    )
    assert out.prompt_version == PROMPT_VERSION_V4_2


def _v4_payload_with_dimensions() -> str:
    """v4.2 payload that emits the six dimension_scores."""
    return json.dumps(
        {
            "prompt_version": "cv_match_v4.2",
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
                    "requirement": "AWS Glue ETL ownership",
                    "priority": "must_have",
                    "evidence_quotes": [
                        "Led AWS Glue / Spark ETL platform for 3 years"
                    ],
                    "evidence_start_char": 0,
                    "evidence_end_char": 47,
                    "reasoning": "exact match in the managed_spark_etl cluster.",
                    "status": "met",
                    "match_tier": "exact",
                    "impact": "Met.",
                    "confidence": "high",
                }
            ],
            "matching_skills": [],
            "missing_skills": [],
            "experience_highlights": [],
            "concerns": [],
            "summary": "Solid fit.",
        }
    )


def test_v4_2_aggregates_cv_fit_from_dimensions_with_archetype_weights():
    """When the LLM emits dimension_scores and the archetype router picks
    the aws_glue archetype, cv_fit should be derived from the archetype's
    weighted dimensions, not the simple (skills + experience) / 2 path.

    aws_glue weights: skills_coverage=0.25, skills_depth=0.25,
    title_trajectory=0.10, seniority_alignment=0.15, industry_match=0.15,
    tenure_pattern=0.10.

    Expected cv_fit:
      0.25*80 + 0.25*75 + 0.10*70 + 0.15*65 + 0.15*60 + 0.10*55
      = 20 + 18.75 + 7 + 9.75 + 9 + 5.5
      = 70.0
    """
    rubric = load_rubric("aws_glue_data_engineer")
    cv = "Led AWS Glue / Spark ETL platform for 3 years"
    client = _StubClient(
        messages=_StubMessages(responses=[_v4_payload_with_dimensions()])
    )
    out = run_cv_match(
        cv_text=cv,
        jd_text=rubric.jd_centroid_text,
        requirements=[],
        client=client,
        skip_cache=True,
        version="v4.2",
    )
    assert isinstance(out, CVMatchOutputV4)
    assert out.dimension_scores is not None
    assert out.dimension_scores.skills_coverage == 80.0
    # Computed by compute_cv_fit_v4_2 with the aws_glue archetype weights.
    assert abs(out.cv_fit_score - 70.0) < 0.01
    # Back-filled v3-compat scores.
    # skills = (80 + 75) / 2 = 77.5
    # experience = (70 + 65 + 60 + 55) / 4 = 62.5
    assert abs(out.skills_match_score - 77.5) < 0.01
    assert abs(out.experience_relevance_score - 62.5) < 0.01
