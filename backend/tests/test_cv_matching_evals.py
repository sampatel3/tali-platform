"""Tests for the eval harness (single scoring path)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.cv_matching import PROMPT_VERSION
from app.cv_matching import archetype_synthesizer
from app.cv_matching.evals import run_evals
from app.cv_matching.evals.run_evals import _build_requirements, run_one


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
    body: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _StubResponse(text=self.body)

    def count_tokens(self, **kwargs):
        @dataclass
        class _C:
            input_tokens: int = 100

        return _C()


@dataclass
class _StubClient:
    messages: _StubMessages


def _build_passing_response(cv_text: str) -> dict:
    aws_quote = "AWS Glue"
    python_quote = "Python"
    return {
        "prompt_version": PROMPT_VERSION,
        "dimension_scores": {
            "skills_coverage": 90.0,
            "skills_depth": 85.0,
            "title_trajectory": 85.0,
            "seniority_alignment": 80.0,
            "industry_match": 85.0,
            "tenure_pattern": 80.0,
        },
        "skills_match_score": 0,
        "experience_relevance_score": 0,
        "requirements_assessment": [
            {
                "requirement_id": "req_1",
                "requirement": "5+ years AWS data pipelines",
                "priority": "must_have",
                "evidence_quotes": [aws_quote],
                "evidence_start_char": cv_text.find(aws_quote),
                "evidence_end_char": cv_text.find(aws_quote) + len(aws_quote),
                "reasoning": "Glue named in candidate experience.",
                "status": "met",
                "match_tier": "exact",
                "impact": "Direct match.",
                "confidence": "high",
            },
            {
                "requirement_id": "req_2",
                "requirement": "Strong Python + SQL",
                "priority": "must_have",
                "evidence_quotes": [python_quote],
                "evidence_start_char": cv_text.find(python_quote),
                "evidence_end_char": cv_text.find(python_quote) + len(python_quote),
                "reasoning": "Python listed as a primary language.",
                "status": "met",
                "match_tier": "exact",
                "impact": "Stated.",
                "confidence": "high",
            },
            {
                "requirement_id": "req_3",
                "requirement": "Banking domain",
                "priority": "strong_preference",
                "evidence_quotes": ["Regional Bank"],
                "evidence_start_char": cv_text.find("Regional Bank"),
                "evidence_end_char": cv_text.find("Regional Bank") + len("Regional Bank"),
                "reasoning": "Worked at a Regional Bank.",
                "status": "met",
                "match_tier": "exact",
                "impact": "Banking experience.",
                "confidence": "high",
            },
        ],
        "matching_skills": ["AWS Glue", "Python", "SQL"],
        "missing_skills": [],
        "experience_highlights": ["7 years AWS data engineering"],
        "concerns": [],
        "summary": "Strong direct match across must-haves and banking domain.",
    }


def _disable_archetype(monkeypatch):
    monkeypatch.setattr(
        archetype_synthesizer, "synthesize_archetype", lambda *a, **kw: None
    )


@pytest.fixture
def placeholder_case() -> dict:
    cases_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "cv_matching"
        / "evals"
        / "golden_cases.yaml"
    )
    import yaml

    cases = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    return cases[0]


# --------------------------------------------------------------------------- #
# Harness internals                                                            #
# --------------------------------------------------------------------------- #


def test_build_requirements_round_trip():
    raw = [
        {
            "id": "r1",
            "requirement": "Python",
            "priority": "must_have",
            "evidence_hints": ["py"],
        },
        {
            "id": "r2",
            "requirement": "AWS",
            "priority": "strong_preference",
        },
    ]
    out = _build_requirements(raw)
    assert len(out) == 2
    assert out[0].id == "r1"
    assert out[0].priority.value == "must_have"
    assert out[0].evidence_hints == ["py"]


def test_run_one_passes_against_stub(monkeypatch, placeholder_case):
    _disable_archetype(monkeypatch)
    cv_text = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "cv_matching"
        / "evals"
        / placeholder_case["cv_file"]
    ).read_text(encoding="utf-8")

    response_body = json.dumps(_build_passing_response(cv_text))
    stub = _StubClient(messages=_StubMessages(body=response_body))

    monkeypatch.setattr(
        "app.cv_matching.runner._resolve_anthropic_client",
        lambda: stub,
    )

    result = run_one(placeholder_case, skip_cache=True)
    assert result.passed, f"failures: {result.failures}"
    assert result.recommendation in ("yes", "strong_yes")
    assert 60 <= result.role_fit_score <= 100


def test_run_one_records_failure_when_must_meet_misses(monkeypatch, placeholder_case):
    _disable_archetype(monkeypatch)
    cv_text = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "cv_matching"
        / "evals"
        / placeholder_case["cv_file"]
    ).read_text(encoding="utf-8")
    payload = _build_passing_response(cv_text)
    payload["requirements_assessment"][0]["status"] = "missing"
    payload["requirements_assessment"][0]["match_tier"] = "missing"
    payload["requirements_assessment"][0]["evidence_quotes"] = []
    payload["requirements_assessment"][0]["evidence_start_char"] = -1
    payload["requirements_assessment"][0]["evidence_end_char"] = -1
    stub = _StubClient(messages=_StubMessages(body=json.dumps(payload)))
    monkeypatch.setattr(
        "app.cv_matching.runner._resolve_anthropic_client",
        lambda: stub,
    )
    result = run_one(placeholder_case, skip_cache=True)
    assert not result.passed
    assert any("req_1" in f for f in result.failures)


def test_main_writes_baseline_snapshot(monkeypatch, tmp_path):
    _disable_archetype(monkeypatch)
    cv_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "cv_matching"
        / "evals"
        / "fixtures"
        / "cvs"
        / "placeholder_eng.txt"
    )
    cv_text = cv_path.read_text(encoding="utf-8")
    body = json.dumps(_build_passing_response(cv_text))
    stub = _StubClient(messages=_StubMessages(body=body))
    monkeypatch.setattr(
        "app.cv_matching.runner._resolve_anthropic_client",
        lambda: stub,
    )

    monkeypatch.setattr(run_evals, "BASELINE_DIR", tmp_path / "baselines")
    monkeypatch.setattr("sys.argv", ["run_evals", "--no-cache"])
    rc = run_evals.main()
    assert rc == 0

    snapshot = list((tmp_path / "baselines").glob("*.json"))
    assert len(snapshot) == 1
    blob = json.loads(snapshot[0].read_text(encoding="utf-8"))
    assert blob["prompt_version"] == PROMPT_VERSION
    assert len(blob["results"]) >= 1


def test_main_baseline_md_writes_markdown(monkeypatch, tmp_path):
    _disable_archetype(monkeypatch)
    cv_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "cv_matching"
        / "evals"
        / "fixtures"
        / "cvs"
        / "placeholder_eng.txt"
    )
    cv_text = cv_path.read_text(encoding="utf-8")
    body = json.dumps(_build_passing_response(cv_text))
    stub = _StubClient(messages=_StubMessages(body=body))
    monkeypatch.setattr(
        "app.cv_matching.runner._resolve_anthropic_client",
        lambda: stub,
    )
    monkeypatch.setattr(run_evals, "BASELINE_DIR", tmp_path / "baselines")
    monkeypatch.setattr(
        "sys.argv", ["run_evals", "--no-cache", "--baseline-md"]
    )
    rc = run_evals.main()
    assert rc == 0

    md_files = list((tmp_path / "baselines").glob("*.md"))
    assert len(md_files) == 1
    md_body = md_files[0].read_text(encoding="utf-8")
    assert "Baseline report" in md_body
    assert "Per-case results" in md_body
