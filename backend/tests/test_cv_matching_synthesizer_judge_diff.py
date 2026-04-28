"""Tests for the agentic additions: archetype synthesizer, judge, baseline diff."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.cv_matching import archetype_synthesizer
from app.cv_matching.archetype_synthesizer import (
    ArchetypeRubric,
    MustHaveArchetype,
    SeniorityAnchors,
    reset_cache,
    synthesize_archetype,
)
from app.cv_matching.calibrators.judge import judge_advance_probability
from app.cv_matching.embeddings import clear_cache as clear_embed_cache


# --------------------------------------------------------------------------- #
# Stub Anthropic client                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class _Block:
    text: str


@dataclass
class _Resp:
    text: str

    @property
    def content(self):
        return [_Block(text=self.text)]


@dataclass
class _Msgs:
    canned: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self.canned) - 1)
        return _Resp(text=self.canned[idx])


@dataclass
class _Client:
    messages: _Msgs


def setup_function(_):
    clear_embed_cache()
    reset_cache()


# --------------------------------------------------------------------------- #
# archetype_synthesizer                                                        #
# --------------------------------------------------------------------------- #


def _valid_rubric_json() -> str:
    return json.dumps(
        {
            "archetype_id": "auto_test_role",
            "description": "A test role family.",
            "jd_centroid_text": "Hire a person to do test things.",
            "must_have_archetypes": [
                {
                    "cluster": "core_skill",
                    "description": "the core skill",
                    "exact_matches": ["TestSkill"],
                    "strong_substitutes": ["RelatedSkill"],
                    "weak_substitutes": ["LooseSkill"],
                    "unrelated": ["UnrelatedSkill"],
                }
            ],
            "seniority_anchors": {
                "band_100": "leader of the role family",
                "band_75": "above-the-bar candidate",
                "band_50": "borderline",
                "band_25": "adjacent stack",
                "band_0": "wrong field entirely",
            },
            "dimension_weights": {
                "skills_coverage": 0.25,
                "skills_depth": 0.20,
                "title_trajectory": 0.15,
                "seniority_alignment": 0.15,
                "industry_match": 0.15,
                "tenure_pattern": 0.10,
            },
        }
    )


def test_synthesize_archetype_returns_rubric_on_success():
    client = _Client(messages=_Msgs(canned=[_valid_rubric_json()]))
    rubric = synthesize_archetype("Hire a person to do test things.", client=client)
    assert isinstance(rubric, ArchetypeRubric)
    assert rubric.archetype_id == "auto_test_role"
    assert any(c.cluster == "core_skill" for c in rubric.must_have_archetypes)


def test_synthesize_archetype_caches_in_lru():
    client = _Client(messages=_Msgs(canned=[_valid_rubric_json()]))
    a = synthesize_archetype("Hire a person.", client=client)
    # Second call with the same JD should hit the LRU (cosine ~1 against
    # the just-cached centroid). No new Sonnet call.
    b = synthesize_archetype("Hire a person.", client=client)
    assert a is not None and b is not None
    assert len(client.messages.calls) == 1
    assert b.archetype_id == a.archetype_id


def test_synthesize_archetype_returns_none_on_invalid_json():
    client = _Client(messages=_Msgs(canned=["not json"]))
    rubric = synthesize_archetype("anything", client=client)
    assert rubric is None


def test_synthesize_archetype_returns_none_on_invalid_schema():
    client = _Client(messages=_Msgs(canned=[json.dumps({"archetype_id": "x"})]))
    rubric = synthesize_archetype("anything", client=client)
    assert rubric is None


def test_synthesize_archetype_empty_jd_returns_none():
    assert synthesize_archetype("", client=_Client(messages=_Msgs(canned=[]))) is None
    assert synthesize_archetype("   ", client=_Client(messages=_Msgs(canned=[]))) is None


def test_archetype_rubric_normalised_weights_sum_to_one():
    r = ArchetypeRubric(
        archetype_id="x",
        description="d",
        jd_centroid_text="j",
        must_have_archetypes=[],
        seniority_anchors=SeniorityAnchors(),
        dimension_weights={"skills_coverage": 0.5},
    )
    norm = r.normalised_dimension_weights()
    assert abs(sum(norm.values()) - 1.0) < 1e-9


# --------------------------------------------------------------------------- #
# judge                                                                        #
# --------------------------------------------------------------------------- #


def test_judge_returns_p_advance():
    client = _Client(messages=_Msgs(canned=[json.dumps({"p_advance": 0.72, "reasoning": "strong"})]))
    p = judge_advance_probability(
        jd_text="JD", cv_text="CV", requirements=[], client=client
    )
    assert p == 0.72


def test_judge_clamps_to_unit_range():
    client = _Client(messages=_Msgs(canned=[json.dumps({"p_advance": 1.7})]))
    p = judge_advance_probability(jd_text="JD", cv_text="CV", client=client)
    assert p == 1.0

    client = _Client(messages=_Msgs(canned=[json.dumps({"p_advance": -0.3})]))
    p = judge_advance_probability(jd_text="JD", cv_text="CV", client=client)
    assert p == 0.0


def test_judge_returns_none_on_invalid_response():
    client = _Client(messages=_Msgs(canned=["not json"]))
    assert judge_advance_probability(jd_text="JD", cv_text="CV", client=client) is None


def test_judge_returns_none_on_missing_field():
    client = _Client(messages=_Msgs(canned=[json.dumps({"reasoning": "no number"})]))
    assert judge_advance_probability(jd_text="JD", cv_text="CV", client=client) is None


# --------------------------------------------------------------------------- #
# baseline_diff                                                                #
# --------------------------------------------------------------------------- #


def test_baseline_diff_writes_markdown(tmp_path):
    from app.cv_matching.evals.baseline_diff import write_markdown_report

    snapshot = tmp_path / "test_v1_20260427T000000Z.json"
    snapshot.write_text(
        json.dumps(
            {
                "prompt_version": "test_v1",
                "timestamp": "20260427T000000Z",
                "results": [
                    {
                        "case_id": "case_a",
                        "passed": True,
                        "recommendation": "yes",
                        "role_fit_score": 75.0,
                        "failures": [],
                        "output": {
                            "dimension_scores": {
                                "skills_coverage": 80.0,
                                "skills_depth": 70.0,
                                "title_trajectory": 70.0,
                                "seniority_alignment": 70.0,
                                "industry_match": 70.0,
                                "tenure_pattern": 70.0,
                            }
                        },
                    },
                    {
                        "case_id": "case_b",
                        "passed": False,
                        "recommendation": "no",
                        "role_fit_score": 30.0,
                        "failures": ["dimension skills_coverage=10 outside [50, 100]"],
                        "output": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    md = write_markdown_report(snapshot)
    body = md.read_text(encoding="utf-8")
    assert "Baseline report — test_v1" in body
    assert "case_a" in body and "case_b" in body
    # Per-dimension stats present (only case_a has dimensions).
    assert "Per-dimension stats" in body
    assert "skills_coverage" in body
    # Score band table present.
    assert "Score band distribution" in body
