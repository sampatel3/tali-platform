"""Test the eval harness runs end-to-end against the placeholder fixture.

Stubs the Anthropic client so we don't hit the real API in CI. Verifies:
- the harness loads golden_cases.yaml
- it produces a CaseResult per case
- the placeholder case passes given a synthesized in-range response
- baseline snapshot is written

Real-fixture regression runs go through the CLI (`python -m
app.cv_matching.evals.run_evals`) and are not part of CI per the
handover ("only run on prompt version changes").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.cv_matching import PROMPT_VERSION
from app.cv_matching.evals import run_evals
from app.cv_matching.evals.run_evals import _build_requirements, run_one


def _stub_response(payload: dict) -> str:
    return json.dumps(payload)


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
    python_quote = "Python (expert)"
    return {
        "prompt_version": PROMPT_VERSION,
        "skills_match_score": 88,
        "experience_relevance_score": 85,
        "requirements_assessment": [
            {
                "requirement_id": "req_1",
                "requirement": "5+ years AWS data pipelines",
                "priority": "must_have",
                "status": "met",
                "evidence_quote": aws_quote,
                "evidence_start_char": cv_text.find(aws_quote),
                "evidence_end_char": cv_text.find(aws_quote) + len(aws_quote),
                "impact": "Direct match.",
                "confidence": "high",
            },
            {
                "requirement_id": "req_2",
                "requirement": "Strong Python + SQL",
                "priority": "must_have",
                "status": "met",
                "evidence_quote": python_quote,
                "evidence_start_char": cv_text.find(python_quote),
                "evidence_end_char": cv_text.find(python_quote) + len(python_quote),
                "impact": "Stated.",
                "confidence": "high",
            },
            {
                "requirement_id": "req_3",
                "requirement": "Banking domain",
                "priority": "strong_preference",
                "status": "met",
                "evidence_quote": "Regional Bank",
                "evidence_start_char": cv_text.find("Regional Bank"),
                "evidence_end_char": cv_text.find("Regional Bank")
                + len("Regional Bank"),
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
    return cases[0]  # the placeholder


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
    assert out[1].priority.value == "strong_preference"


def test_run_one_passes_against_stub_anthropic(monkeypatch, placeholder_case):
    """End-to-end: harness invokes runner, runner uses stubbed client."""
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
    """If req_1 reports missing, the case should fail the must_meet check."""
    cv_text = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "cv_matching"
        / "evals"
        / placeholder_case["cv_file"]
    ).read_text(encoding="utf-8")
    payload = _build_passing_response(cv_text)
    payload["requirements_assessment"][0]["status"] = "missing"
    payload["requirements_assessment"][0]["evidence_quote"] = ""
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
    """`run_evals.main()` should write a snapshot file with prompt_version in name."""
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

    # Redirect baseline output to tmp_path so we don't pollute the repo.
    monkeypatch.setattr(run_evals, "BASELINE_DIR", tmp_path / "baselines")

    monkeypatch.setattr("sys.argv", ["run_evals", "--no-cache"])
    rc = run_evals.main()
    assert rc == 0

    snapshot = list((tmp_path / "baselines").glob("*.json"))
    assert len(snapshot) == 1
    blob = json.loads(snapshot[0].read_text(encoding="utf-8"))
    assert blob["prompt_version"] == PROMPT_VERSION
    assert len(blob["results"]) >= 1


def test_prompt_version_constants_coexist():
    from app.cv_matching import PROMPT_VERSION, PROMPT_VERSION_V4

    assert PROMPT_VERSION == "cv_match_v3.0"
    assert PROMPT_VERSION_V4 == "cv_match_v4.1"
    assert PROMPT_VERSION != PROMPT_VERSION_V4


def _build_v4_passing_response(cv_text: str) -> dict:
    """Same fixture as _build_passing_response but in v4 schema shape."""
    aws_quote = "AWS Glue"
    python_quote = "Python"
    return {
        "prompt_version": "cv_match_v4.1",
        "skills_match_score": 90,
        "experience_relevance_score": 85,
        "requirements_assessment": [
            {
                "requirement_id": "req_1",
                "requirement": "AWS Glue",
                "priority": "must_have",
                "evidence_quotes": [aws_quote],
                "evidence_start_char": cv_text.find(aws_quote),
                "evidence_end_char": cv_text.find(aws_quote) + len(aws_quote),
                "reasoning": "Candidate names AWS Glue explicitly in their experience.",
                "status": "met",
                "match_tier": "exact",
                "impact": "Required tool clearly named.",
                "confidence": "high",
            },
            {
                "requirement_id": "req_2",
                "requirement": "Python",
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
                "evidence_end_char": cv_text.find("Regional Bank")
                + len("Regional Bank"),
                "reasoning": "Worked at a Regional Bank for 3 years.",
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


def test_run_one_v4_dispatches_v4_pipeline(monkeypatch, placeholder_case):
    cv_text = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "cv_matching"
        / "evals"
        / placeholder_case["cv_file"]
    ).read_text(encoding="utf-8")

    response_body = json.dumps(_build_v4_passing_response(cv_text))
    stub = _StubClient(messages=_StubMessages(body=response_body))

    monkeypatch.setattr(
        "app.cv_matching.runner._resolve_anthropic_client",
        lambda: stub,
    )

    result = run_one(placeholder_case, skip_cache=True, version="v4.1")
    assert result.passed, f"failures: {result.failures}"
    # The output blob should carry the v4 prompt version + the v4 per-req shape.
    assert result.output["prompt_version"] == "cv_match_v4.1"
    assert "evidence_quotes" in result.output["requirements_assessment"][0]
    assert "match_tier" in result.output["requirements_assessment"][0]


def test_main_version_v4_writes_v4_snapshot(monkeypatch, tmp_path):
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
    body = json.dumps(_build_v4_passing_response(cv_text))
    stub = _StubClient(messages=_StubMessages(body=body))
    monkeypatch.setattr(
        "app.cv_matching.runner._resolve_anthropic_client",
        lambda: stub,
    )
    monkeypatch.setattr(run_evals, "BASELINE_DIR", tmp_path / "baselines")
    monkeypatch.setattr(
        "sys.argv", ["run_evals", "--no-cache", "--version", "v4.1"]
    )
    rc = run_evals.main()
    assert rc == 0

    snapshots = list((tmp_path / "baselines").glob("cv_match_v4.1_*.json"))
    assert len(snapshots) == 1
    blob = json.loads(snapshots[0].read_text(encoding="utf-8"))
    assert blob["prompt_version"] == "cv_match_v4.1"
    assert blob["version"] == "v4.1"


def test_main_version_both_writes_two_snapshots(monkeypatch, tmp_path):
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
    v3_body = json.dumps(_build_passing_response(cv_text))
    v4_body = json.dumps(_build_v4_passing_response(cv_text))

    # Stub returns the v3 body first time, v4 body second time. The harness
    # runs v3 across all cases, then v4 across all cases.
    bodies = [v3_body, v4_body]
    call_idx = {"i": 0}

    @dataclass
    class _ToggleResponse:
        text: str

        @property
        def content(self):
            return [_StubBlock(text=self.text)]

        @property
        def usage(self):
            return _StubUsage()

    @dataclass
    class _ToggleMessages:
        def create(self, **kwargs):
            body = bodies[min(call_idx["i"], len(bodies) - 1)]
            call_idx["i"] += 1
            return _ToggleResponse(text=body)

        def count_tokens(self, **kwargs):
            from dataclasses import dataclass as _dc

            @_dc
            class _C:
                input_tokens: int = 100

            return _C()

    @dataclass
    class _ToggleClient:
        messages: _ToggleMessages = field(default_factory=_ToggleMessages)

    monkeypatch.setattr(
        "app.cv_matching.runner._resolve_anthropic_client",
        lambda: _ToggleClient(),
    )
    monkeypatch.setattr(run_evals, "BASELINE_DIR", tmp_path / "baselines")
    monkeypatch.setattr(
        "sys.argv", ["run_evals", "--no-cache", "--version", "both"]
    )
    rc = run_evals.main()
    assert rc == 0

    v3_snaps = list((tmp_path / "baselines").glob("cv_match_v3.0_*.json"))
    v4_snaps = list((tmp_path / "baselines").glob("cv_match_v4.1_*.json"))
    assert len(v3_snaps) == 1
    assert len(v4_snaps) == 1
